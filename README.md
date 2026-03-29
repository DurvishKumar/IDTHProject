# Blockchain-based E-Voting Web Application

This project is a complete beginner-friendly e-voting system built with Flask, SQLite, Bootstrap, and JSON blockchain storage.

## Folder Structure

```text
IDTHProject/
├── app.py
├── blockchain.json
├── database.db
├── requirements.txt
├── render.yaml
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── main.js
└── templates/
    ├── admin_dashboard.html
    ├── admin_login.html
    ├── base.html
    ├── home.html
    ├── register.html
    ├── results.html
    ├── vote.html
    └── voter_login.html
```

## Main Features

- Admin login
- Election management
- Candidate management
- Voter registration with Aadhaar hashing and password hashing
- Voter login with election status checks
- One vote per voter per election
- Blockchain storage of votes in `blockchain.json`
- Tampering simulation and blockchain verification
- Result display with tampering warning logic

## Default Admin Credentials

- Admin ID: `admin`
- Password: `Admin@123`

## How to Run Locally

1. Create a virtual environment:

```bash
python -m venv venv
```

2. Activate it:

```bash
venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Start the app:

```bash
python app.py
```

5. Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

If your project folder is inside OneDrive and SQLite shows a disk I/O error, start the app with a writable override path:

```powershell
$env:DATABASE_PATH="$env:TEMP\evoting.db"
python app.py
```

## GitHub Push Steps

```bash
git init
git add .
git commit -m "Build blockchain-based e-voting app"
git branch -M main
git remote add origin https://github.com/your-username/your-repo.git
git push -u origin main
```

## Render Deployment

1. Push the project to GitHub.
2. In Render, create a new Python Web Service.
3. Select the repository.
4. Use:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python app.py`
5. Add environment variable `SECRET_KEY`.
6. Deploy.

## Custom Domain

1. Open the Render service.
2. Go to Settings.
3. Add a custom domain.
4. Copy the DNS records Render gives you into your domain provider panel.
5. Wait for SSL to finish provisioning.

## Notes

- `database.db` is created automatically when the app starts.
- `blockchain.json` stores all blocks in readable JSON format.
- Results are blocked if tampering is detected unless the admin chooses to proceed.
