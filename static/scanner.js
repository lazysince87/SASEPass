/* -------------------------------------------------------
   SASEPass Scanner & Event Page Logic
   ------------------------------------------------------- */

/**
 * Detect QR code type based on content format.
 */
function isUUID(str) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(str);
}

function isFernetToken(str) {
    // Fernet tokens start with 'gAAAAA' and are base64-encoded, typically 100+ chars
    return str.startsWith('gAAAAA') && str.length > 100;
}

/**
 * Log workshop attendance for encrypted QR codes.
 */
function logWorkshopAttendance(qrData, event, wrapperId, innerId) {
    fetch("/log_workshop_attendance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ qr_data: qrData, event: event }),
    })
        .then(function (r) { return r.json(); })
        .then(function (data) { showResult(wrapperId, innerId, data); });
}

/**
 * Initialise the QR scanner, manual selection, admin tools,
 * and live‑polling for the given event.
 */
function initScanner(eventName) {
    // ── QR Scanner ──────────────────────────────────────
    const html5Qr = new Html5Qrcode("qr-reader");
    let scanning = false;
    let cooldown = false;
    let currentFacingMode = "environment"; // "environment" = back, "user" = front

    function startCamera() {
        html5Qr
            .start(
                { facingMode: currentFacingMode },
                { fps: 10, qrbox: { width: 250, height: 250 } },
                function onScan(decodedText) {
                    if (cooldown) return;
                    cooldown = true;

                    // Detect QR type and route accordingly
                    if (isFernetToken(decodedText)) {
                        // Encrypted workshop QR code
                        logWorkshopAttendance(decodedText, eventName, "scan-result", "scan-result-inner");
                    } else {
                        // Standard UUID-based hacker QR code
                        logAttendance(decodedText, eventName, "scan-result", "scan-result-inner");
                    }

                    setTimeout(function () { cooldown = false; }, 2500);
                }
            )
            .then(function () { scanning = true; })
            .catch(function (err) {
                console.warn("Camera not available:", err);
            });
    }

    startCamera();

    // ── Flip Camera ────────────────────────────────────
    var flipBtn = document.getElementById("flip-camera-btn");
    if (flipBtn) {
        flipBtn.addEventListener("click", function () {
            html5Qr.stop().then(function () {
                scanning = false;
                currentFacingMode = currentFacingMode === "environment" ? "user" : "environment";
                startCamera();
            }).catch(function (err) {
                console.warn("Error stopping camera:", err);
            });
        });
    }

    // ── Load Eligible Users ─────────────────────────────
    var allUsers = [];

    fetch("/get_eligible_users/" + encodeURIComponent(eventName))
        .then(function (r) { return r.json(); })
        .then(function (users) {
            allUsers = users;
            populateSelect(users);
        });

    function populateSelect(users) {
        var sel = document.getElementById("manual-select");
        sel.innerHTML = '<option value="">-- Select a hacker --</option>';
        users.forEach(function (u) {
            var opt = document.createElement("option");
            opt.value = u.guest_id;
            opt.textContent = u.display_name;
            sel.appendChild(opt);
        });
    }

    // ── Manual Search Filter (debounced) ────────────────
    var searchInput = document.getElementById("manual-search");
    var debounceTimer = null;

    searchInput.addEventListener("input", function () {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(function () {
            var q = searchInput.value.trim().toLowerCase();
            if (!q) {
                populateSelect(allUsers);
                return;
            }
            var filtered = allUsers.filter(function (u) {
                return u.display_name.toLowerCase().indexOf(q) !== -1;
            });
            populateSelect(filtered);
        }, 300);
    });

    // ── Manual Submit ───────────────────────────────────
    document.getElementById("manual-submit").addEventListener("click", function () {
        var gid = document.getElementById("manual-select").value;
        if (!gid) return;
        logAttendance(gid, eventName, "manual-result", "manual-result-inner");
    });

    // ── Admin: Add Hacker ───────────────────────────────
    var addBtn = document.getElementById("add-hacker-btn");
    if (addBtn) {
        addBtn.addEventListener("click", function () {
            var name = document.getElementById("add-name").value.trim();
            var email = document.getElementById("add-email").value.trim();
            if (!name || !email) return;

            fetch("/add_hacker", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: name, email: email }),
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    showResult("add-result", "add-result-inner", data);
                    if (data.status === "success" || data.status === "warning") {
                        document.getElementById("add-name").value = "";
                        document.getElementById("add-email").value = "";
                        // Refresh eligible users
                        fetch("/get_eligible_users/" + encodeURIComponent(eventName))
                            .then(function (r) { return r.json(); })
                            .then(function (users) {
                                allUsers = users;
                                populateSelect(users);
                            });
                    }
                });
        });
    }

    // ── Live Stats Polling (every 5 s) ──────────────────
    setInterval(function () {
        fetch("/api/stats/" + encodeURIComponent(eventName))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                document.getElementById("stat-here").textContent = d.here;
                document.getElementById("stat-total").textContent = d.total;
                document.getElementById("stat-event").textContent = d.event_count;
                document.getElementById("stat-workshop").textContent = d.workshop_count;

                // Refresh hacker activity log
                var log = document.getElementById("activity-log");
                if (d.recent_activity && d.recent_activity.length) {
                    log.innerHTML = "";
                    d.recent_activity.forEach(function (entry) {
                        var div = document.createElement("div");
                        div.className = "flex items-center justify-between px-3 py-2 rounded-lg bg-gray-800/60 text-sm group";
                        var leftCol = '<div class="flex flex-col">' +
                            '<span class="text-gray-200">' + escapeHtml(entry.name) + "</span>" +
                            '<span class="text-gray-500 text-xs">' + escapeHtml(entry.created_at || "") + "</span>" +
                            '</div>';
                        var rightCol = '';
                        if (window.IS_ADMIN) {
                            rightCol = '<button onclick="removeHackerFromActivity(\'' + escapeHtml(entry.hacker_id) + '\', \'' + escapeHtml(eventName) + '\')" class="text-red-400 hover:text-red-300 opacity-0 group-hover:opacity-100 transition px-2 py-1 bg-red-900/40 rounded-md">Remove</button>';
                        }
                        div.innerHTML = leftCol + rightCol;
                        log.appendChild(div);
                    });
                } else {
                    log.innerHTML = '<p class="text-gray-500 text-sm py-4 text-center">No hacker activity yet.</p>';
                }

                // Refresh workshop activity log
                var workshopLog = document.getElementById("workshop-log");
                if (d.workshop_activity && d.workshop_activity.length) {
                    workshopLog.innerHTML = "";
                    d.workshop_activity.forEach(function (entry) {
                        var div = document.createElement("div");
                        div.className = "flex items-center justify-between px-3 py-2 rounded-lg bg-purple-900/20 text-sm group";
                        var leftCol = '<div class="flex flex-col">' +
                            '<span class="text-gray-200">' + escapeHtml(entry.email) + "</span>" +
                            '<span class="text-gray-500 text-xs">' + escapeHtml(entry.created_at || "") + "</span>" +
                            '</div>';
                        var rightCol = '';
                        if (window.IS_ADMIN) {
                            rightCol = '<button onclick="removeWorkshopAttendee(\'' + escapeHtml(entry.email) + '\', \'' + escapeHtml(eventName) + '\')" class="text-red-400 hover:text-red-300 opacity-0 group-hover:opacity-100 transition px-2 py-1 bg-red-900/40 rounded-md">Remove</button>';
                        }
                        div.innerHTML = leftCol + rightCol;
                        workshopLog.appendChild(div);
                    });
                } else {
                    workshopLog.innerHTML = '<p class="text-gray-500 text-sm py-4 text-center">No workshop attendance yet.</p>';
                }
            });
    }, 5000);
}

// ── Helpers ─────────────────────────────────────────────

function logAttendance(guestId, event, wrapperId, innerId) {
    fetch("/log_attendance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guest_id: guestId, event: event }),
    })
        .then(function (r) { return r.json(); })
        .then(function (data) { showResult(wrapperId, innerId, data); });
}

function showResult(wrapperId, innerId, data) {
    var wrapper = document.getElementById(wrapperId);
    var inner = document.getElementById(innerId);
    wrapper.classList.remove("hidden");

    // Reset classes
    inner.className = "px-4 py-3 rounded-lg text-sm font-medium";

    if (data.status === "success") {
        inner.classList.add("bg-emerald-900/40", "border", "border-emerald-700", "text-emerald-300");
    } else if (data.status === "warning") {
        inner.classList.add("bg-yellow-900/40", "border", "border-yellow-700", "text-yellow-300");
    } else {
        inner.classList.add("bg-red-900/40", "border", "border-red-700", "text-red-300");
    }

    inner.textContent = data.message;

    // Auto-hide after 4 seconds
    setTimeout(function () {
        wrapper.classList.add("hidden");
    }, 4000);
}

function escapeHtml(str) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

function removeHackerFromActivity(guestId, eventName) {
    if (!confirm("Are you sure you want to remove this hacker's check-in for " + eventName + "?")) return;
    fetch("/remove_attendance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guest_id: guestId, event: eventName }),
    })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.status === "error") alert(data.message);
        });
}

function removeWorkshopAttendee(email, eventName) {
    if (!confirm("Are you sure you want to remove " + email + " from " + eventName + "?")) return;
    fetch("/remove_workshop_attendance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, event: eventName }),
    })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.status === "error") alert(data.message);
        });
}
