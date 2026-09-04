import os
import sys
import re
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
        context = browser.new_context()
        page = context.new_page()

        # Placeholders for captured authentication info
        auth_token = [None]
        employee_id = [None]

        def handle_request(request):
            # Intercept Authorization header from API calls
            if "api.payfit.com" in request.url:
                headers = request.headers
                if "authorization" in headers and headers["authorization"].startswith("Bearer "):
                    auth_token[0] = headers["authorization"]

        page.on("request", handle_request)

        # 1. Navigate to Payfit Login
        logger.info("Navigating to https://app.payfit.com...")
        page.goto("https://app.payfit.com")

        # Wait for the login page (Auth0 redirects)
        page.wait_for_load_state("networkidle")

        # 2. Enter email
        logger.info("Entering login email...")
        email_selector = "input[type='email'], input[name='email'], input[name='username']"
        page.wait_for_selector(email_selector, timeout=15000)
        page.fill(email_selector, login)

        # Press Enter on email (extremely robust way to submit forms in Google/Auth0)
        page.press(email_selector, "Enter")
        page.wait_for_timeout(1000)

        # Fallback: Click Next/Continue if press Enter didn't trigger navigation
        continue_btn = page.locator(
            "button[type='submit'], "
            "button:has-text('Continue'), "
            "button:has-text('Se connecter'), "
            "button:has-text('Next'), "
            "button:has-text('Suivant'), "
            "button:has-text('Connexion')"
        )
        if continue_btn.count() > 0:
            try:
                continue_btn.first.click(timeout=2000)
            except Exception:
                pass

        page.wait_for_timeout(1500)

        # 3. Enter password
        logger.info("Entering login password...")
        # Avoid Google's input[name="hiddenPassword"] by filtering out names containing 'hidden' or aria-hidden
        password_selector = "input[type='password']:not([name*='hidden']):not([aria-hidden='true']), input[name='password']:not([name*='hidden'])"
        page.wait_for_selector(password_selector, timeout=15000)
        page.fill(password_selector, password)

        # Submit password
        logger.info("Submitting password...")
        page.press(password_selector, "Enter")
        page.wait_for_timeout(1000)

        # Fallback: Click login/submit button
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

        # 4. Handle 2FA if required
        page.wait_for_load_state("networkidle")

        # Check if we are on a 2FA page (look for an OTP/code input field)
        # Auth0 TOTP pages typically have input[id='code'] or similar
        mfa_selector = "input[autocomplete='one-time-code'], input[name='code'], input[id='code'], input[placeholder='Code']"

        # We will wait a short moment to see if MFA is prompted
        mfa_prompted = False
        try:
            page.wait_for_selector(mfa_selector, timeout=5000)
            mfa_prompted = True
        except Exception:
            logger.info(
                "No 2FA screen detected or it timed out. Continuing...")

        if mfa_prompted:
            if not totp_secret:
                logger.error(
                    "2FA / TOTP is requested by Payfit but TOTP_SECRET is not configured.")
                sys.exit(1)

            logger.info("2FA prompt detected. Generating TOTP code...")
            try:
                totp = pyotp.TOTP(totp_secret.replace(" ", ""))
                code = totp.now()
                logger.info(
                    "TOTP code generated successfully. Entering code...")
                page.fill(mfa_selector, code)

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
                            logger.info(
                                "Clicking the 'Remember this device' label...")
                            remember_label.first.click(timeout=3000)
                    except Exception as e:
                        logger.warning(
                            f"Could not click 'Remember this device' label: {e}")

                # Submit MFA
                mfa_submit = page.locator(
                    "button[type='submit'], button:has-text('Continue'), button:has-text('Se connecter'), button:has-text('Vérifier')")
                mfa_submit.first.click()
            except Exception as e:
                logger.error(f"Failed to generate or enter TOTP code: {e}")
                sys.exit(1)

        # 5. Wait for successful redirection to employee dashboard or account selection
        logger.info(
            "Waiting for redirection to employee space or account selection...")
        try:
            # Wait a short moment to see if we land on change-account or direct employee space
            page.wait_for_load_state("networkidle")

            # Wait for auth_token[0] to be populated so we can call the accounts API
            logger.info(
                "Waiting for initial Bearer Token to fetch account details...")
            for _ in range(20):
                if auth_token[0]:
                    break
                page.wait_for_timeout(500)

            if not auth_token[0]:
                raise ValueError(
                    "Could not capture initial Bearer Token from network requests.")

            # Query individuals-bff/accounts API
            logger.info(
                "Fetching available companies/accounts from Payfit API...")
            accounts_headers = {
                "Authorization": auth_token[0],
                "Accept": "*/*",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            try:
                r = requests.get(
                    "https://api.payfit.com/individuals-bff/accounts", headers=accounts_headers)
                r.raise_for_status()
                accounts_data = r.json()
            except Exception as e:
                logger.error(
                    f"Failed to fetch available companies from API: {e}")
                sys.exit(1)

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

            company_name_env = os.getenv("COMPANY_NAME")
            selected_company = None

            if company_name_env:
                company_name_env_clean = company_name_env.strip().upper()
                for c in companies:
                    if c["name"].strip().upper() == company_name_env_clean:
                        selected_company = c
                        break
                if not selected_company:
                    available_names = ", ".join(
                        [f"'{c['name']}'" for c in companies])
                    logger.error(
                        f"COMPANY_NAME '{company_name_env}' was specified but not found in your account. Available companies: {available_names}")
                    sys.exit(1)
            else:
                if len(companies) > 1:
                    available_names = ", ".join(
                        [f"'{c['name']}'" for c in companies])
                    logger.error(
                        f"Multiple companies found ({available_names}) but no COMPANY_NAME was specified. Please set the COMPANY_NAME environment variable.")
                    sys.exit(1)
                elif len(companies) == 1:
                    selected_company = companies[0]
                else:
                    logger.error("No companies found in Payfit account info.")
                    sys.exit(1)

            logger.info(
                f"Target company: '{selected_company['name']}' (Employee ID: {selected_company['employee_id']})")
            employee_id[0] = selected_company["employee_id"]

            # Support both direct employee URL or change-account page
            for _ in range(15):
                current_url = page.url
                if "change-account" in current_url:
                    logger.info(
                        f"Account selection page detected. Selecting card for '{selected_company['name']}'...")

                    clicked = False
                    company_name = selected_company['name']

                    # Try to locate the 'Accès Collaborateur' card following the company name via XPath
                    xpath_selector = f"xpath=//*[text()='{company_name}']/following::*[contains(text(), 'Accès Collaborateur')]"
                    card = page.locator(xpath_selector)
                    if card.count() > 0:
                        logger.info(
                            f"Found card for '{company_name}' using exact text XPath. Clicking...")
                        card.first.click()
                        clicked = True

                    if not clicked:
                        xpath_selector_contains = f"xpath=//*[contains(text(), '{company_name}')]/following::*[contains(text(), 'Accès Collaborateur')]"
                        card_contains = page.locator(xpath_selector_contains)
                        if card_contains.count() > 0:
                            logger.info(
                                f"Found card for '{company_name}' using contains XPath. Clicking...")
                            card_contains.first.click()
                            clicked = True

                    if not clicked:
                        # Fallback to general click if XPath fails
                        profile_card = page.locator(
                            "text='Accès Collaborateur'")
                        if profile_card.count() > 0:
                            logger.warning(
                                f"Could not locate custom card for '{company_name}'. Clicking the first available 'Accès Collaborateur' card...")
                            profile_card.first.click()
                            clicked = True

                    if not clicked:
                        # Fallback: click first button/link/card on account choice page
                        logger.warning(
                            "Could not find profile cards. Attempting fallback click on first link/button...")
                        fallbacks = page.locator("a, button, role=button")
                        if fallbacks.count() > 0:
                            fallbacks.first.click()

                    page.wait_for_timeout(2000)
                    break
                elif "/employee/" in current_url:
                    break
                page.wait_for_timeout(1000)

            # Ensure the browser navigates to the correct employee page URL
            target_employee_url_re = re.compile(rf"/employee/{employee_id[0]}")
            page.wait_for_url(target_employee_url_re, timeout=30000)
            logger.info(
                f"Successfully loaded employee space! Current URL: {page.url}")
        except Exception as e:
            logger.error(f"Login or redirection failed: {e}")
            # Take screenshot for debugging if it fails
            screenshot_path = download_dir / "login_error.png"
            page.screenshot(path=str(screenshot_path))
            logger.error(f"Saved error screenshot to {screenshot_path}")
            sys.exit(1)

        # 6. Extract the Bearer Token
        # Let's navigate directly to the payslips page to trigger the network calls
        payslips_url = f"https://app.payfit.com/employee/{employee_id[0]}/payslips"
        logger.info(f"Navigating to payslips page: {payslips_url}...")
        page.goto(payslips_url)
        page.wait_for_load_state("networkidle")

        # Let's wait up to 10 seconds for the token to be captured
        for _ in range(20):
            if auth_token[0]:
                break
            page.wait_for_timeout(500)

        if not auth_token[0]:
            logger.error("Failed to intercept api.payfit.com Bearer Token.")
            sys.exit(1)

        logger.info("Successfully captured API Bearer Token.")

        # 7. Query payslips API & Download missing PDFs via Playwright browser session
        headers = {
            "Authorization": auth_token[0],
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        list_url = f"https://api.payfit.com/payroll-bff/employee-space/payslip/definitive?employeeId={employee_id[0]}"
        logger.info(f"Fetching payslips list from API: {list_url}")

        try:
            response = requests.get(list_url, headers=headers)
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
            logger.info(
                f"Navigating to payslip page for period {abs_month} ({filename})...")
            try:
                page.goto(
                    f"https://app.payfit.com/employee/{employee_id[0]}/payslips/{abs_month}")
                page.wait_for_load_state("networkidle")

                # Locate and wait for the download button to be visible
                download_btn = page.locator("#payslipDownload")
                download_btn.wait_for(state="visible", timeout=15000)

                logger.info(
                    "Clicking the download button and waiting for PDF download...")
                with page.expect_download(timeout=30000) as download_info:
                    download_btn.click()

                download = download_info.value
                download.save_as(str(target_file))
                logger.info(f"Successfully downloaded {filename} via browser.")
                downloaded_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to download payslip for period {abs_month} via browser: {e}")

        logger.info("Synchronization complete.")
        logger.info(
            f"Total processed: {len(payslips_data)} | Downloaded: {downloaded_count} | Skipped: {skipped_count}")
        browser.close()


if __name__ == "__main__":
    run()
