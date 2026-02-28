# SASEPass

## How to Run Locally

### 1. Prerequisites
Ensure you have Python 3 installed on your system.

### 2. Setup Environment Variables
Copy the example environment file and fill in your Supabase credentials and other configuration details:
```bash
cp .env.example .env
```

### 3. Install Dependencies
It is recommended to use a virtual environment. Run the following commands:

**Mac or Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows:**
```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run the Application
Start the Flask development server:
```bash
python api/index.py
```
The application will be available at `http://127.0.0.1:5000/`.

