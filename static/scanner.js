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
            .then(function () {
                scanning = true;
                // iOS Safari fix: ensure video has playsinline attribute
                var video = document.querySelector("#qr-reader video");
                if (video) {
                    video.setAttribute("playsinline", "true");
                    video.setAttribute("webkit-playsinline", "true");
                }
            })
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
    inner.className = "px-6 py-4 rounded-xl text-lg font-bold text-center";

    if (data.status === "success") {
        inner.classList.add("bg-emerald-600", "text-white", "shadow-lg", "shadow-emerald-500/50");
        // Screen flash
        flashScreen("emerald");
        // Vibrate on mobile
        if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
    } else if (data.status === "warning") {
        inner.classList.add("bg-yellow-600", "text-white", "shadow-lg", "shadow-yellow-500/50");
        flashScreen("yellow");
        if (navigator.vibrate) navigator.vibrate(200);
    } else {
        inner.classList.add("bg-red-600", "text-white", "shadow-lg", "shadow-red-500/50");
        flashScreen("red");
        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    }

    // Add icon + message
    var icon = "";
    if (data.status === "success") {
        icon = '<svg class="inline w-6 h-6 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path></svg>';
    } else if (data.status === "warning") {
        icon = '<svg class="inline w-6 h-6 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>';
    } else {
        icon = '<svg class="inline w-6 h-6 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M6 18L18 6M6 6l12 12"></path></svg>';
    }
    inner.innerHTML = icon + escapeHtml(data.message);

    // Auto-hide after 4 seconds
    setTimeout(function () {
        wrapper.classList.add("hidden");
    }, 4000);
}

function flashScreen(color) {
    var flash = document.createElement("div");
    flash.className = "fixed inset-0 pointer-events-none z-50 transition-opacity duration-300";

    if (color === "emerald") {
        flash.style.backgroundColor = "rgba(16, 185, 129, 0.4)";
    } else if (color === "yellow") {
        flash.style.backgroundColor = "rgba(234, 179, 8, 0.4)";
    } else {
        flash.style.backgroundColor = "rgba(239, 68, 68, 0.4)";
    }

    document.body.appendChild(flash);

    setTimeout(function () {
        flash.style.opacity = "0";
        setTimeout(function () {
            flash.remove();
        }, 300);
    }, 200);
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
