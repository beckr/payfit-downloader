# Payfit Downloader ⬇ 

A Dockerized Python tool to automatically sync your payslips from Payfit to a local directory with 2FA/TOTP support.


## TL;DR

Run the container with full-filled parameters

```bash
docker run --rm \
  -v /path/to/local/downloads:/app/payfit/downloads \
  -e DOWNLOAD_DIR=/app/payfit/downloads \
  -e LOG_FILE_PATH=/app/payfit/downloads/downloader.log \
  -e LOGIN=your.email@example.com \
  -e PASSWORD=YourPassword \
  -e TOTP_SECRET="your totp secret" \
  -e COMPANY_NAME="MY_COMPANY" \
  rphbck/payfit-downloader:latest
```

*Company name is generally optional*

 > Note: if your password contains a shell reserved word (like `$` or `!`), you must escape it like this 
`-e PASSWORD=Pas\$w0rd\! \`

---

## Prerequisites
- Your **Payfit login credentials** (email + password).
- Your **Payfit 2FA / TOTP Secret Key** (optional, but highly recommended if 2FA is active). Go to [How to get your TOTP Secret Key from Payfit](#how-to-get-your-totp-secret-key-from-payfit) section for directions.
- Docker
---

## Key Features
- **Headless Browser Automation (Playwright)**: Automatically logs in and bypasses 2FA checks natively.
- **Direct API Synchronization**: Intercepts active Bearer tokens and communicates directly with Payfit APIs for lightweight and ultra-fast PDF downloads.
- **One-Way Sync (Deduplication)**: Compares the files in your local directory against the remote list. If a payslip is already downloaded, it skips it and logs it cleanly as skipped.
- **Beautiful Filenames**: Decodes Payfit's absolute monthly period system into a human-readable format, e.g., `payslip_2026_08.pdf`.

---

# Local development

## Prerequisites
- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) package manager

## Configuration

The recommended way to configure the downloader is using a `.env` file in the project root directory.

Create a file named `.env` and fill it with your credentials:

```ini
# Payfit credentials
LOGIN=your.email@example.com
PASSWORD=YourSecurePassword

# Your 2FA TOTP secret (textual key, spaces will be automatically removed)
TOTP_SECRET=abcd efgh ijkl mnop qrst uvwx yz12 3456

# Optional: Company / Establishment Name
# Required if you have multiple companies on your Payfit account.
# Optional (automatically chosen) if you have exactly 1 company.
COMPANY_NAME=MY_COMPANY

# Optional: Host directory to store downloads (defaults to ./downloads)
DOWNLOAD_DIR_HOST=/home/user/downloads
```

### How to get your TOTP Secret Key from Payfit
1. Log in to Payfit in a normal web browser.
2. Go to your **Profile Settings** -> **Security / Multi-Factor Authentication (MFA)**.
3. If you have already set up 2FA, you might need to temporarily disable it and set it up again to see the secret key or use a method below.
4. When setting up a new Google Authenticator / Microsoft Authenticator app on Payfit, it will display a **QR Code** and a **textual secret key** (e.g. `abcd efgh ...`).
5. Copy this textual key and paste it as the `TOTP_SECRET` in your `.env` file. You can then finish the 2FA setup in your Authenticator app using that key.

#### How to extract a TOTP Secret Key if you already have registered the MFA
##### Google authenticator
1. Go to Google authenticator
2. In the menu, transfer codes
3. Export codes
4. Select only Payfit (unchecked all but Payfit)
5. Save the QR code displayed
6. Choose to **NOT** delete exported codes
7. Use an extractor like [extract_otp_secret_keys](https://scito.ch/content/extract-secret-keys-google-authenticator-qr-export)

##### Authenticator Pro (the best!)
Check if you don't know this authenticator [Official website](https://stratumauth.com/) | [Github](https://github.com/stratumauth/app)

1. Go to Authenticator Pro
2. On Payfit item touch the three dot menu
3. Go to "Modify information"
4. Go to "Display advanced options"
5. Say ok to the warning
6. Un-hide and copy the secret key
7. Cancel

##### Microsoft Authenticator
1. It is **not** possible otherwise by resetting the 2FA... Check for directions if you need it [How to Get Microsoft Authenticator Secret Key [2026 Full Guide]
](https://www.youtube.com/watch?v=LU0oFz6BdnE)
---

## Usage

### 1. Build the Docker Image
To build the image locally, run the provided build script:

```bash
./tools/build.sh
```

This builds and pushes (multi-arch) the Docker image `rphbck/payfit-downloader`, tagged with both the project version and `latest`.

The version is defined only in `pyproject.toml`. To release a new version, bump it with uv (this also updates `uv.lock`) and rebuild:

```bash
uv version --bump patch   # or minor / major, or an explicit version: uv version 0.2.0
./tools/build.sh
```

### 2. Run the Downloader
To run the synchronization, execute the run script:

```bash
./tools/run.sh
```

This loads your configuration from `.env`, creates a `downloads/` folder on your host machine (unless overridden via `DOWNLOAD_DIR_HOST`), mounts it to the container, and downloads any new payslips.

---



## Architecture & Internals
- **Package Manager**: Built with [uv](https://github.com/astral-sh/uv) for lightning-fast and reproducible Python environment compilation.
- **Interception**: Playwright initializes headlessly and runs the standard browser login process. As soon as the browser accesses the Payfit application, a background thread intercepts standard Auth0 authentication headers (`Authorization: Bearer ...`) and grabs the `employeeId` from the URL.
- **Direct Fetch**: The browser is closed immediately after token interception. Then, standard lightweight `requests` HTTP calls fetch the full array of payslips and download the PDF contents. This avoids UI sluggishness and maximizes performance.


[![better commits is enabled](https://img.shields.io/badge/better--commits-enabled?style=for-the-badge&logo=git&color=a6e3a1&logoColor=D9E0EE&labelColor=302D41)](https://github.com/Everduin94/better-commits)