from selectolax.parser import HTMLParser
import re
from datetime import date
import requests
import os
import sys
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import AppConfig

config_file_path = os.path.join(os.path.dirname(__file__), 'config.yml')
config = AppConfig(config_file_path)

class Result:
    def __init__(self, xmldoc):
        self.xmldoc = xmldoc

    @property
    def server_url(self):
        if not hasattr(self, '_server_url'):
            result = self.xmldoc.css_first('serverUrl')
            self._server_url = result.text() if result is not None else None
        return self._server_url

    @property
    def session_id(self):
        if not hasattr(self, '_session_id'):
            result = self.xmldoc.css_first('sessionId')
            self._session_id = result.text() if result is not None else None
        return self._session_id

    @property
    def org_id(self):
        if not hasattr(self, '_org_id'):
            result = self.xmldoc.css_first('organizationId')
            self._org_id = result.text() if result is not None else None
        return self._org_id

class SfError(Exception):
    def __init__(self, resp):
        self.resp = resp

    def inspect(self):
        print(self.resp.text)

    def __str__(self):
        return self.inspect()

    def __repr__(self):
        return self.inspect()

def headers(login):
    return {
        'Cookie': f'oid={login.org_id}; sid={login.session_id}',
        'X-SFDC-Session': login.session_id
    }

def file_name(url=None):
    datestamp = date.today().strftime('%Y-%m-%d')
    uid_string = ''
    if url:
        match = re.search(r'.*fileName=(.*)\.ZIP.*', url)
        if match:
            uid_string = f"-{match.group(1)}"
    return f"salesforce-{datestamp}{uid_string}.ZIP"

def progress_percentage(current, total):
    return int((current / total) * 100)

def login():
    print("Logging in...")
    path = '/services/Soap/u/28.0'

    pwd_token_encoded = config.sales_force_passwd_and_sec_token.replace('&', '&amp;')

    initial_data = f'''<?xml version="1.0" encoding="utf-8" ?>
    <env:Envelope xmlns:xsd="http://www.w3.org/2001/XMLSchema"
        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
    <env:Body>
        <n1:login xmlns:n1="urn:partner.soap.sforce.com">
        <n1:username>{config.sales_force_user_name}</n1:username>
        <n1:password>{pwd_token_encoded}</n1:password>
        </n1:login>
    </env:Body>
    </env:Envelope>'''

    initial_headers = {
        'Content-Type': 'text/xml; charset=UTF-8',
        'SOAPAction': 'login'
    }

    url = 'https://login.salesforce.com'  # Change this URL if needed
    resp = requests.post(url + path, data=initial_data, headers=initial_headers)

    if resp.status_code == 200:
        xmldoc = HTMLParser(resp.text)
        return Result(xmldoc)
    else:
        raise SfError(resp)
    
def download_index(login):
    print("Downloading index...")
    path = 'https://' + config.sales_force_site + '/servlet/servlet.OrgExport'
    response = requests.post(url=path, headers=headers(login))
    return response.text.strip()

def get_download_size(login, url):
    print("Getting download size...")
    response = requests.head(url, headers=headers(login))
    content_length = response.headers.get('Content-Length')
    return int(content_length) if content_length is not None else 0

def download_file(login, url, expected_size):
    printing_interval = 10
    interval_type = "percentage"
    last_printed_value = None
    size = 0
    fn = file_name(url)
    file_path = os.path.join(config.data_directory, fn)

    print(f"Downloading {fn}...")
    
    with open(file_path, "wb") as f:
        response = requests.get(url, headers=headers(login), stream=True)
        response.raise_for_status()

        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                size += len(chunk)
                last_printed_value = print_progress(size, expected_size, printing_interval, last_printed_value, interval_type)
    
    print(f"\nFinished downloading {fn}!")

    if size != expected_size:
        raise ValueError(f"Size didn't match. Expected: {expected_size} Actual: {size}")

def print_progress(size, expected_size, interval, previous_printed_interval, interval_type="seconds"):
    percent_file_complete = int((size / expected_size) * 100)
    
    if interval_type == "percentage":
        previous_printed_interval = previous_printed_interval or 0
        current_value = percent_file_complete
    elif interval_type == "seconds":
        previous_printed_interval = previous_printed_interval or time.time()
        current_value = time.time()
    
    next_interval = previous_printed_interval + interval
    
    if current_value >= next_interval:
        timestamp = time.strftime('%Y-%m-%d-%H-%M-%S')
        print(f"{timestamp}: {percent_file_complete}% complete ({size} of {expected_size})")
        return next_interval
    
    return previous_printed_interval

def send_email(subject, data):
    # Create an SMTP connection
    try:
        smtp_conn = smtplib.SMTP(config.smtp_server, config.smtp_port)
        smtp_conn.starttls()
        smtp_conn.login(config.smtp_username, config.smtp_password)
    except Exception as e:
        print(f"SMTP connection error: {e}")
        return

    # Create and send the email
    try:
        msg = MIMEMultipart()
        msg['From'] = config.email_address_from
        msg['To'] = config.email_address_to
        msg['Subject'] = subject

        # Attach the email content
        msg.attach(MIMEText(data, 'plain'))

        # Send the email
        smtp_conn.sendmail(config.email_address_from, config.email_address_to, msg.as_string())
        print(f"Email sent successfully: {subject}")
    except Exception as e:
        print(f"Email sending error: {e}")

    # Close the SMTP connection
    smtp_conn.quit()

def email_success(file_name, size):
    subject = "Salesforce backup successfully downloaded"
    data = f"Salesforce backup saved into {file_name}, size {size}"
    send_email(subject, data)

def email_failure(url, error_msg):
    subject = "Salesforce backup download failed"
    data = f"Failed to download {url}. {error_msg}"
    send_email(subject, data)

def main():
    result = login()
    urls = download_index(result).split("\n")
    if urls[0] == '':
        sys.exit('No Urls found!')

    print("All urls:")
    print(urls)
    print("")

    if not os.path.exists(config.data_directory):
        os.makedirs(config.data_directory)

    for url in urls:
        fn = file_name(url)
        file_path = os.path.join(config.data_directory, fn)
        retry_count = 0
        newUrl = 'https://' + config.sales_force_site + url
        while retry_count < 5:
            try:
                print(f"Working on: {newUrl}")
                expected_size = get_download_size(result, newUrl)
                print(f"Expected size: {expected_size}")
                fs = os.path.getsize(file_path) if os.path.exists(file_path) else None
                if fs and fs == expected_size:
                    print(f"File {fn} exists and is the right size. Skipping.")
                else:
                    download_file(result, newUrl, expected_size)
                    email_success(file_path, expected_size)
                break
            except Exception as e:
                print(f"Error: {e}")
                if retry_count < 4:
                    retry_count += 1
                    print("Retrying (retry_count of 5)...")
                else:
                    email_failure(newUrl, str(e))
                    break
    print("Done!")

if __name__ == '__main__':
    main()