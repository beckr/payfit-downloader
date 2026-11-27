import os
import sys
import re
import time
import logging
from pathlib import Path
import pyotp
import requests
from playwright.sync_api import sync_playwright

# Setup logging
log_file_path = os.getenv("LOG_FILE_PATH")
download_dir_env = os.getenv("DOWNLOAD_DIR", "/app/payfit/downloads")
download_dir = Path(download_dir_env)

# Create download directory if not exists
download_dir.mkdir(parents=True, exist_ok=True)

# Configure logging to write to both stdout and log_file_path (if provided)
log_handlers = [logging.StreamHandler(sys.stdout)]
if log_file_path:
    log_file = Path(log_file_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=log_handlers
)

logger = logging.getLogger("payfit-downloader")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Timeouts are generous on purpose: the script may run behind a slow VPN
DEFAULT_TIMEOUT_MS = 30000
NAVIGATION_TIMEOUT_MS = 60000
HTTP_TIMEOUT_S = 30

LOGIN_ATTEMPTS = 2
PROFILE_SELECTION_ATTEMPTS = 3
DOWNLOAD_ATTEMPTS = 2

# Labels of the employee access card on the profile selection page (FR and EN UI)
EMPLOYEE_CARD_LABELS = ["Accès Collaborateur",
                        "Accès collaborateur", "Employee access", "Employee"]

MFA_SELECTOR = "input[autocomplete='one-time-code'], input[name='code'], input[id='code'], input[placeholder='Code']"


class ConfigError(Exception):
    """Configuration problem: retrying will not help."""


def get_date_from_absolute_month(abs_month):
    """
    Payfit uses a 2015-based absolute month indexing system.
    Year = 2015 + (absolute_month - 1) // 12
    Month = (absolute_month - 1) % 12 + 1
    """
    try:
        abs_month = int(abs_month)
        year = 2015 + (abs_month - 1) // 12
        month = (abs_month - 1) % 12 + 1
        return year, month
    except Exception as e:
        logger.error(f"Error parsing absolute month {abs_month}: {e}")
        return None, None


def xpath_literal(value):
    """Quote a string for use inside an XPath expression."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


def save_diagnostics(page, name):
    """Save a screenshot and the HTML of the current page for debugging."""
    try:
        logger.error(f"Current URL: {page.url}")
        screenshot_path = download_dir / f"{name}.png"
        page.screenshot(path=str(screenshot_path))
        logger.error(f"Saved error screenshot to {screenshot_path}")
        html_path = download_dir / f"{name}.html"
        html_path.write_text(page.content(), encoding="utf-8")
        logger.error(f"Saved error page HTML to {html_path}")
    except Exception as e:
        logger.error(f"Could not save diagnostics: {e}")


def wait_for_token(page, auth_token, timeout_ms=DEFAULT_TIMEOUT_MS):
    """Wait until a Bearer token has been intercepted from api.payfit.com."""
    deadline = time.monotonic() + timeout_ms / 1000
    while not auth_token[0] and time.monotonic() < deadline:
        page.wait_for_timeout(500)
    return auth_token[0]


def click_submit_fallback(page):
    # Fallback: Click Next/Continue/Login if pressing Enter didn't trigger navigation
    submit_btn = page.locator(
        "button[type='submit'], "
        "button:has-text('Continue'), "
        "button:has-text('Se connecter'), "
        "button:has-text('Next'), "
        "button:has-text('Suivant'), "
        "button:has-text('Connexion')"
    )
    if submit_btn.count() > 0:
        try:
            submit_btn.first.click(timeout=2000)
        except Exception:
            pass


def handle_mfa(page, totp_secret):
    if not totp_secret:
        raise ConfigError(
            "2FA / TOTP is requested by Payfit but TOTP_SECRET is not configured.")

    logger.info("2FA prompt detected. Generating TOTP code...")
    totp = pyotp.TOTP(totp_secret.replace(" ", ""))
    code = totp.now()
    logger.info("TOTP code generated successfully. Entering code...")
    page.fill(MFA_SELECTOR, code)

    # Try to select and check the "Remember this device" option if present
    remember_me = page.locator("input[type='checkbox']")
    if remember_me.count() > 0:
        logger.info(
            "Found 'Remember this device' checkbox. Attempting to check it...")
        try:
            # Force check to bypass label pointer interception and avoid timeout
            remember_me.first.check(timeout=1000, force=True)
        except Exception as e:
            logger.warning(
                f"Could not check 'Remember this device' checkbox directly: {e}")
            try:
                remember_me.first.click(timeout=1000, force=True)
            except Exception as e_click:
                logger.warning(
                    f"Failed fallback click on checkbox: {e_click}")

    # Click label or span containing text as a fallback if not already checked
    remember_label = page.locator(
        "label:has-text('Remember this device'), span:has-text('Remember this device'), p:has-text('Remember this device')")
    if remember_label.count() > 0:
        try:
            is_checked = False
            if remember_me.count() > 0:
                is_checked = remember_me.first.is_checked()

            if not is_checked:
                logger.info("Clicking the 'Remember this device' label...")
                remember_label.first.click(timeout=3000)
        except Exception as e:
            logger.warning(
                f"Could not click 'Remember this device' label: {e}")

    # Submit MFA
    mfa_submit = page.locator(
        "button[type='submit'], button:has-text('Continue'), button:has-text('Se connecter'), button:has-text('Vérifier')")
    mfa_submit.first.click()


def select_company(auth_token):
    """Query the accounts API and return the company matching COMPANY_NAME."""
    logger.info("Fetching available companies/accounts from Payfit API...")
    accounts_headers = {
        "Authorization": auth_token,
        "Accept": "*/*",
        "User-Agent": USER_AGENT
    }
    r = requests.get("https://api.payfit.com/individuals-bff/accounts",
                     headers=accounts_headers, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    accounts_data = r.json()

    # Parse available companies
    companies = []
    for item in accounts_data:
        comp_info = item.get("companyInfo")
        if comp_info:
            companies.append({
                "name": comp_info.get("name"),
                "employee_id": item.get("account", {}).get("employeeId"),
                "company_id": comp_info.get("id"),
                "login_description": comp_info.get("loginDescription")
            })

    company_names = [c['name'] for c in companies]
    logger.info(f"Available companies on account: {company_names}")
    available_names = ", ".join([f"'{c['name']}'" for c in companies])

    company_name_env = os.getenv("COMPANY_NAME")
    if company_name_env:
        company_name_env_clean = company_name_env.strip().upper()
        for c in companies:
            if c["name"].strip().upper() == company_name_env_clean:
                return c
        raise ConfigError(
            f"COMPANY_NAME '{company_name_env}' was specified but not found in your account. Available companies: {available_names}")

    if len(companies) > 1:
        raise ConfigError(
            f"Multiple companies found ({available_names}) but no COMPANY_NAME was specified. Please set the COMPANY_NAME environment variable.")
    if len(companies) == 1:
        return companies[0]
    raise ValueError("No companies found in Payfit account info.")


def select_profile(page, company_name, employee_id):
    """
    Pick the employee profile of the target company on the change-account page.
    The profile list is loaded asynchronously (slowly behind a VPN), so wait for it
    and retry with a page reload before falling back to direct navigation.
    """
    target_re = re.compile(rf"/employee/{re.escape(employee_id)}")
    name_literal = xpath_literal(company_name)

    for attempt in range(1, PROFILE_SELECTION_ATTEMPTS + 1):
        try:
            # Exact match so that 'ACME' does not match 'ACME GROUP'
            company_label = page.get_by_text(company_name, exact=True).first
            company_label.wait_for(state="visible")

            card = None
            for label in EMPLOYEE_CARD_LABELS:
                candidate = page.locator(
                    f"xpath=//*[normalize-space(text())={name_literal}]/following::*[contains(text(), {xpath_literal(label)})]")
                if candidate.count() > 0:
                    logger.info(
                        f"Found '{label}' card for '{company_name}'. Clicking...")
                    card = candidate.first
                    break

            if card is None:
                logger.warning(
                    f"No employee access card found for '{company_name}'. Clicking the company name instead...")
                card = company_label

            card.click()
            page.wait_for_url(target_re)
            return
        except Exception as e:
            if target_re.search(page.url):
                return
            logger.warning(
                f"Profile selection attempt {attempt}/{PROFILE_SELECTION_ATTEMPTS} failed: {e}")
            if attempt < PROFILE_SELECTION_ATTEMPTS:
                page.reload()

    # Last resort: the session is authenticated, try to open the employee space directly
    employee_url = f"https://app.payfit.com/employee/{employee_id}"
    logger.warning(
        f"Could not select profile on the page. Navigating directly to {employee_url}...")
    page.goto(employee_url)
    page.wait_for_url(target_re)
    # Make sure the app did not redirect us back to the profile selection
    page.wait_for_timeout(3000)
    if not target_re.search(page.url):
        raise ValueError(
            f"Direct navigation to employee space was redirected to {page.url}")


def login_and_open_employee_space(page, auth_token, login, password, totp_secret):
    """Log in, handle 2FA and profile selection. Returns the employee ID."""
    # 1. Navigate to Payfit Login
    logger.info("Navigating to https://app.payfit.com...")
    page.goto("https://app.payfit.com")

    # 2. Enter email (wait for the login page, Auth0 redirects)
    logger.info("Entering login email...")
    email_selector = "input[type='email'], input[name='email'], input[name='username']"
    page.wait_for_selector(email_selector)
    page.fill(email_selector, login)

    # Press Enter on email (extremely robust way to submit forms in Google/Auth0)
    page.press(email_selector, "Enter")
    page.wait_for_timeout(1000)
    click_submit_fallback(page)
    page.wait_for_timeout(1500)

    # 3. Enter password
    logger.info("Entering login password...")
    # Avoid Google's input[name="hiddenPassword"] by filtering out names containing 'hidden' or aria-hidden
    password_selector = "input[type='password']:not([name*='hidden']):not([aria-hidden='true']), input[name='password']:not([name*='hidden'])"
    page.wait_for_selector(password_selector)
    page.fill(password_selector, password)

    # Submit password
    logger.info("Submitting password...")
    page.press(password_selector, "Enter")
    page.wait_for_timeout(1000)
    click_submit_fallback(page)

    # 4. Handle 2FA if required: wait until either the OTP field shows up or the
    # app is already calling the API (no 2FA requested)
    mfa_input = page.locator(MFA_SELECTOR).first
    deadline = time.monotonic() + DEFAULT_TIMEOUT_MS / 1000
    mfa_prompted = False
    while time.monotonic() < deadline:
        if mfa_input.is_visible():
            mfa_prompted = True
            break
        if auth_token[0]:
            break
        page.wait_for_timeout(500)

    if mfa_prompted:
        handle_mfa(page, totp_secret)
    else:
        logger.info("No 2FA screen detected. Continuing...")

    # 5. Wait for successful redirection to employee dashboard or account selection
    logger.info(
        "Waiting for redirection to employee space or account selection...")

    # Wait for the Bearer token so we can call the accounts API
    logger.info("Waiting for initial Bearer Token to fetch account details...")
    if not wait_for_token(page, auth_token):
        raise ValueError(
            "Could not capture initial Bearer Token from network requests.")

    selected_company = select_company(auth_token[0])
    employee_id = selected_company["employee_id"]
    logger.info(
        f"Target company: '{selected_company['name']}' (Employee ID: {employee_id})")

    # Support both direct employee URL or change-account page
    page.wait_for_url(re.compile(r"change-account|/employee/"))
    if "change-account" in page.url:
        logger.info(
            f"Account selection page detected. Selecting card for '{selected_company['name']}'...")
        select_profile(page, selected_company["name"], employee_id)
    elif not re.search(rf"/employee/{re.escape(employee_id)}", page.url):
        # Landed on another company's employee space
        page.goto(f"https://app.payfit.com/employee/{employee_id}")

    # Ensure the browser navigates to the correct employee page URL
    page.wait_for_url(re.compile(rf"/employee/{re.escape(employee_id)}"))
    logger.info(f"Successfully loaded employee space! Current URL: {page.url}")
    return employee_id


def run():
    # Load and validate environment variables
    login = os.getenv("LOGIN")
    password = os.getenv("PASSWORD")
    totp_secret = os.getenv("TOTP_SECRET")

    if not login or not password:
        logger.error("Environment variables LOGIN and PASSWORD must be set.")
        sys.exit(1)

    if not totp_secret:
        logger.warning(
            "TOTP_SECRET is not set. 2FA step will be skipped or may fail if Payfit requires it.")

    logger.info("Starting Payfit Downloader...")

    with sync_playwright() as p:
        logger.info("Launching browser...")
        browser = p.chromium.launch(headless=True)

        # Placeholder for captured authentication info
        auth_token = [None]

        def handle_request(request):
            # Intercept Authorization header from API calls
            if "api.payfit.com" in request.url:
                headers = request.headers
                if "authorization" in headers and headers["authorization"].startswith("Bearer "):
                    auth_token[0] = headers["authorization"]

        page = None
        employee_id = None
        for attempt in range(1, LOGIN_ATTEMPTS + 1):
            auth_token[0] = None
            # Force a French locale/timezone so the UI is the same whatever the
            # IP geolocation (e.g. VPN exit node abroad)
            context = browser.new_context(
                locale="fr-FR",
                timezone_id="Europe/Paris",
                extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"}
            )
            page = context.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT_MS)
            page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
            page.on("request", handle_request)

            try:
                employee_id = login_and_open_employee_space(
                    page, auth_token, login, password, totp_secret)
                break
            except ConfigError as e:
                logger.error(str(e))
                browser.close()
                sys.exit(1)
            except Exception as e:
                logger.error(
                    f"Login or redirection failed (attempt {attempt}/{LOGIN_ATTEMPTS}): {e}")
                save_diagnostics(page, "login_error")
                context.close()
                if attempt == LOGIN_ATTEMPTS:
                    browser.close()
                    sys.exit(1)
                logger.info("Retrying login with a fresh browser session...")

        # 6. Extract the Bearer Token
        # Let's navigate directly to the payslips page to trigger the network calls
        payslips_url = f"https://app.payfit.com/employee/{employee_id}/payslips"
        logger.info(f"Navigating to payslips page: {payslips_url}...")
        page.goto(payslips_url)

        if not wait_for_token(page, auth_token):
            logger.error("Failed to intercept api.payfit.com Bearer Token.")
            sys.exit(1)

        logger.info("Successfully captured API Bearer Token.")

        # 7. Query payslips API & Download missing PDFs via Playwright browser session
        headers = {
            "Authorization": auth_token[0],
            "Accept": "application/json",
            "User-Agent": USER_AGENT
        }

        list_url = f"https://api.payfit.com/payroll-bff/employee-space/payslip/definitive?employeeId={employee_id}"
        logger.info(f"Fetching payslips list from API: {list_url}")

        try:
            response = requests.get(
                list_url, headers=headers, timeout=HTTP_TIMEOUT_S)
            response.raise_for_status()
            payslips_data = response.json()
        except Exception as e:
            logger.error(f"Failed to fetch payslips list from API: {e}")
            sys.exit(1)

        # Standard payslips response is usually a list of objects.
        # Let's verify and process each.
        if not isinstance(payslips_data, list):
            # Fallback in case it's wrapped in an object
            if isinstance(payslips_data, dict) and "payslips" in payslips_data:
                payslips_data = payslips_data["payslips"]
            elif isinstance(payslips_data, dict) and "data" in payslips_data:
                payslips_data = payslips_data["data"]
            else:
                logger.error(
                    f"Unexpected API response format: {payslips_data}")
                sys.exit(1)

        logger.info(f"Found {len(payslips_data)} payslips in Payfit.")

        downloaded_count = 0
        skipped_count = 0
        failed_count = 0

        for item in payslips_data:
            # We need to find the absolute month (the period ID)
            # We'll check various standard fields
            abs_month = None
            for key in ["absoluteMonth", "payPeriod", "period", "id"]:
                if key in item:
                    abs_month = item[key]
                    break

            if abs_month is None:
                logger.warning(
                    f"Could not find period key in payslip item: {item}")
                continue

            # Format filename using Year and Month
            year, month = get_date_from_absolute_month(abs_month)
            if year is None or month is None:
                # Fallback to absolute month in filename if calculations fail
                filename = f"payslip_period_{abs_month}.pdf"
            else:
                filename = f"payslip_{year}_{month:02d}.pdf"

            target_file = download_dir / filename

            # Deduplication check
            if target_file.exists():
                logger.info(
                    f"File skipped: {filename} already exists in {download_dir_env}")
                skipped_count += 1
                continue

            # Download the missing payslip via Playwright browser download to completely bypass 403 Forbidden signature issues
            for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
                logger.info(
                    f"Navigating to payslip page for period {abs_month} ({filename})...")
                try:
                    page.goto(
                        f"https://app.payfit.com/employee/{employee_id}/payslips/{abs_month}")

                    # Locate and wait for the download button to be visible
                    download_btn = page.locator("#payslipDownload")
                    download_btn.wait_for(state="visible")

                    logger.info(
                        "Clicking the download button and waiting for PDF download...")
                    with page.expect_download(timeout=NAVIGATION_TIMEOUT_MS) as download_info:
                        download_btn.click()

                    download = download_info.value
                    download.save_as(str(target_file))
                    logger.info(
                        f"Successfully downloaded {filename} via browser.")
                    downloaded_count += 1
                    break
                except Exception as e:
                    logger.error(
                        f"Failed to download payslip for period {abs_month} via browser (attempt {attempt}/{DOWNLOAD_ATTEMPTS}): {e}")
                    if attempt == DOWNLOAD_ATTEMPTS:
                        save_diagnostics(page, f"download_error_{abs_month}")
                        failed_count += 1

        logger.info("Synchronization complete.")
        logger.info(
            f"Total processed: {len(payslips_data)} | Downloaded: {downloaded_count} | Skipped: {skipped_count} | Failed: {failed_count}")
        browser.close()

        if failed_count:
            sys.exit(1)


if __name__ == "__main__":
    run()
