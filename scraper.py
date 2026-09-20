import csv
import os
import re
import sys
from datetime import datetime
from time import sleep

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

USE_FIREFOX = True # False for Chrome

USE_DRIVER_MANAGER = False # True to download the driver with webdriver-manager instead of Selenium Manager

AMAZON_DOMAIN = 'www.amazon.in' # e.g. www.amazon.com, www.amazon.co.uk

START_YEAR = 2014

END_YEAR = datetime.now().year

DEBUG = False # True to dump the page source when a page cannot be parsed

PAGE_TIMEOUT = 30 # seconds to wait for a page of orders to render

# Physical orders are numbered 402-1234567-1234567, digital ones D01-1234567-1234567.
ORDER_ID_RE = re.compile(r'\b[A-Z0-9]{3}-\d{7}-\d{7}\b')
MONEY_RE = re.compile(r'\d[\d,]*\.?\d*')
ITEMS_IN_ORDER_RE = re.compile(r'(\d+)\s+items?\s+in\s+this\s+order', re.I)
DATE_RES = [
    re.compile(r'^\d{1,2}\s+\w+\s+\d{4}$'),
    re.compile(r'^\w+\s+\d{1,2},\s*\d{4}$'),
]

# Amazon renames these classes with every redesign, so every lookup tries the
# current markup first and falls back to the older ones.
ORDER_CARD_SELECTORS = [
    '.order-card.js-order-card',
    'li.order-card__list',
    '.a-box-group.order.js-order-card',
    '.a-box-group.a-spacing-base.order.js-order-card',
    '.js-order-card',
    '.order',
]
ORDER_HEADER_SELECTORS = [
    '.order-header',
    '.a-box.a-color-offset-background',
    '.a-box-group > .a-box:first-child',
]
ITEM_TITLE_SELECTORS = [
    '.yohtmlc-product-title',
    '.yohtmlc-item a[href*="/dp/"], .yohtmlc-item a[href*="/gp/product/"]',
    'a.a-link-normal[href*="/gp/product/"]',
    'a.a-link-normal[href*="/dp/"]',
    '.a-fixed-left-grid-col.yohtmlc-item.a-col-right .a-row',
]
STATUS_SELECTORS = [
    '.yohtmlc-shipment-status-primaryText',
    '.delivery-box__primary-text',
]
# Cancelled orders carry no total at all, so the status is the only way to tell
# them apart from a genuine zero-value order. Matched against the start of a line,
# which keeps brand lines ("Amazon Now delivery") and the "ORDER PLACED" header
# label out of the column.
STATUS_KEYWORDS = (
    'cancelled', 'canceled', 'order cancelled', 'service cancelled',
    'delivered', 'delivery attempted', 'out for delivery', 'arriving',
    'return', 'refunded', 'replacement', 'replaced',
    'shipped', 'dispatched', 'not yet shipped', 'preparing for dispatch',
    'closed due to', 'payment', 'declined', 'cannot display',
    'successful', 'trip completed', 'journey completed',
)
# "Return window closed on 12 Jan" is a note under the item, not an order status.
STATUS_IGNORED = ('return window',)
# Every item sits in its own tile holding its name and quantity badge.
ITEM_BOX_SELECTORS = [
    '.item-box',
    '.a-fixed-left-grid-inner',
]
QTY_SELECTORS = [
    '.product-image__qty', # current: a badge over the thumbnail, only when quantity > 1
    '.od-item-view-qty',
    '.item-view-qty',
    '[class*="item-view-qty"]',
]
ORDER_FIELDS = ['Date', 'Number', 'Units', 'Amount', 'Details', 'Name', 'Status']
ITEM_FIELDS = ['Date', 'Details', 'Name', 'Units', 'Status']
OTHER_ITEMS = 'Other items in this order'


# Windows consoles default to cp1252, which cannot encode the rupee sign or
# most product names; without this, printing a summary raises UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, OSError):
    pass


def write_csv(filename, data):
    with open(filename, 'w', encoding='utf-8', newline='') as csvfile:
        csvwriter = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL, escapechar='\\')
        csvwriter.writerow(ORDER_FIELDS)
        csvwriter.writerows(order[:len(ORDER_FIELDS)] for order in data)


def write_items_csv(filename, data):
    # One row per item; Details is the order number, so rows join back to the order CSVs.
    # Item prices are not shown on the order list, so the amount stays in the order CSVs.
    with open(filename, 'w', encoding='utf-8', newline='') as csvfile:
        csvwriter = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL, escapechar='\\')
        csvwriter.writerow(ITEM_FIELDS)
        for order in data:
            for name, quantity in order[7]:
                csvwriter.writerow([order[0], order[4], name, quantity, order[6]])


def first_match(scope, selectors):
    for selector in selectors:
        try:
            found = scope.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        if found:
            return found
    return []


def text_of(element):
    try:
        return element.text.strip()
    except WebDriverException:
        return ''


def parse_money(value):
    if not value:
        return 0.0
    match = MONEY_RE.search(value.replace(',', '').replace(' ', ''))
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def value_after_label(lines, *labels):
    # The header renders each field as a label line followed by its value line
    # ("Order placed" / "12 January 2025"), which survives class renames.
    wanted = [label.lower() for label in labels]
    for index, line in enumerate(lines[:-1]):
        if line.strip().lower().rstrip(':') in wanted:
            return lines[index + 1].strip()
    return ''


def header_lines(card):
    header = first_match(card, ORDER_HEADER_SELECTORS)
    scope = header[0] if header else card
    return [line for line in text_of(scope).splitlines() if line.strip()]


def title_text(scope):
    for selector in ITEM_TITLE_SELECTORS:
        try:
            elements = scope.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for element in elements:
            text = text_of(element)
            if text:
                return text.splitlines()[0].strip()
    return ''


def image_name(scope):
    # Grocery orders (Amazon Fresh / Amazon Now) show items as image-only tiles:
    # there is no product title and the /dp/ links carry no text, so the name
    # lives only in the thumbnail's alt attribute.
    try:
        images = scope.find_elements(By.CSS_SELECTOR, 'img')
    except WebDriverException:
        return ''
    for image in images:
        try:
            alt = (image.get_attribute('alt') or '').strip()
        except WebDriverException:
            continue
        # The delivery brand logo sits outside the item tiles, but skip it anyway.
        if alt and 'brand image' not in alt.lower():
            return alt
    return ''


def item_quantity(scope):
    # Amazon only renders a quantity badge when the quantity is greater than one.
    for element in first_match(scope, QTY_SELECTORS):
        match = re.search(r'\d+', text_of(element))
        if match:
            return int(match.group(0))
    return 1


def parse_items(card):
    # Read each tile on its own so a quantity badge stays attached to its item.
    items = []
    for container in first_match(card, ITEM_BOX_SELECTORS):
        name = title_text(container) or image_name(container)
        if name:
            items.append([name, item_quantity(container)])
    if items:
        return items

    # Layouts without item tiles: fall back to the titles anywhere on the card.
    for selector in ITEM_TITLE_SELECTORS:
        try:
            elements = card.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        seen = set()
        for element in elements:
            text = text_of(element)
            name = text.splitlines()[0].strip() if text else ''
            if name and name not in seen:
                seen.add(name)
                items.append([name, 1])
        if items:
            return items
    return items


def is_cancelled(order):
    return order[6].lower().startswith(('cancelled', 'canceled'))


def first_status_line(lines):
    for line in lines:
        line = line.strip()
        lowered = line.lower()
        if line and lowered.startswith(STATUS_KEYWORDS) and not lowered.startswith(STATUS_IGNORED):
            return line
    return ''


def order_status(card, card_text, header_text):
    for element in first_match(card, STATUS_SELECTORS):
        status = first_status_line(text_of(element).splitlines())
        if status:
            return status
    # Cancelled orders render the word on its own line instead of in a status box.
    # Skip the header so its "ORDER PLACED" label is not mistaken for a status.
    header_set = {line.strip() for line in header_text.splitlines() if line.strip()}
    return first_status_line([line for line in card_text.splitlines()
                              if line.strip() not in header_set])


def is_fresh_order(card, card_text):
    if 'amazon fresh' in card_text.lower():
        return True
    # Grocery orders are branded only by the logo image on the card.
    for image in first_match(card, ['img']):
        try:
            alt = image.get_attribute('alt') or ''
        except WebDriverException:
            continue
        if 'fresh' in alt.lower():
            return True
    return False


def parse_order(card):
    lines = header_lines(card)
    header_text = '\n'.join(lines)
    card_text = text_of(card)

    # Digital subscriptions label the date "Subscription charged on".
    date = value_after_label(lines, 'order placed', 'ordered on', 'order placed on',
                             'subscription charged on', 'subscription started on')
    total = parse_money(value_after_label(lines, 'total', 'order total', 'grand total'))
    order_id_match = ORDER_ID_RE.search(header_text) or ORDER_ID_RE.search(card_text)
    order_id = order_id_match.group(0) if order_id_match else ''

    if not date:
        # Digital and legacy cards sometimes omit the label row.
        for line in lines:
            if any(pattern.match(line.strip()) for pattern in DATE_RES):
                date = line.strip()
                break
    if not total:
        for element in first_match(card, ['.yohtmlc-order-total', '.a-column.a-span2']):
            text = text_of(element)
            amount = parse_money(text.splitlines()[-1] if text else '')
            if amount:
                total = amount
                break

    items = parse_items(card)
    number = len(items)
    units = sum(quantity for _, quantity in items)

    bulk = ITEMS_IN_ORDER_RE.search(card_text)
    if bulk:
        # Older Fresh orders collapse their contents into "N items in this order".
        number = max(number, int(bulk.group(1)))
        units = max(units, number)
        if not items:
            items = [['Amazon Fresh' if is_fresh_order(card, card_text) else '', units]]
    if not number:
        number = units = 1
    if not items:
        items = [['', units]]

    names = ' | '.join(name for name, _ in items if name)
    listed = sum(quantity for _, quantity in items)
    if units > listed:
        # Keep the items CSV adding up to the order's units when not every item is listed.
        items.append([OTHER_ITEMS, units - listed])

    return [date, number, units, total, order_id, names,
            order_status(card, card_text, header_text), items]


def dump_debug(wd, tag):
    if not DEBUG:
        return
    path = os.path.abspath(f'debug-{tag}.html')
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(wd.page_source)
        print('  Saved page source to', path)
    except OSError as error:
        print('  Could not save page source:', error)


def wait_for_orders(wd):
    def ready(driver):
        if first_match(driver, ORDER_CARD_SELECTORS):
            return True
        body = text_of(driver.find_element(By.TAG_NAME, 'body')).lower()
        return 'orders placed in' in body or 'no orders' in body or '0 orders' in body

    try:
        WebDriverWait(wd, PAGE_TIMEOUT).until(ready)
    except WebDriverException:
        pass


def ensure_signed_in(wd):
    if '/ap/signin' in wd.current_url:
        input('Session expired. Sign in again, then press Enter...')
        wd.refresh()
        wait_for_orders(wd)


def orders_url(year, start_index):
    # The old /gp/your-account/order-history?orderFilter=year-N endpoint now
    # redirects here and silently drops the filter; timeFilter is the current name.
    url = (f'https://{AMAZON_DOMAIN}/your-orders/orders'
           f'?timeFilter=year-{year}&digitalOrders=1&unifiedOrders=1')
    if start_index:
        url += f'&startIndex={start_index}'
    return url


def all_orders(wd, year):
    data = []
    seen = set()
    start_index = 0
    page = 0
    while True:
        wd.get(orders_url(year, start_index))
        ensure_signed_in(wd)
        wait_for_orders(wd)
        cards = first_match(wd, ORDER_CARD_SELECTORS)
        if not cards:
            if page == 0:
                print(f'  No orders found for {year}')
                dump_debug(wd, f'{year}-empty')
            break

        new_on_page = 0
        for card in cards:
            try:
                row = parse_order(card)
            except WebDriverException:
                continue
            key = row[4] or tuple(str(field) for field in row[:4] + row[5:])
            if key in seen:
                continue
            seen.add(key)
            data.append(row)
            new_on_page += 1

        if not new_on_page:
            break
        start_index += len(cards)
        page += 1
        if first_match(wd, ['.a-disabled.a-last', 'li.a-disabled.a-last']):
            break
        sleep(1)

    if data and not any(row[3] for row in data):
        print(f'  Warning: no order totals could be read for {year}; the page layout may have changed')
        dump_debug(wd, f'{year}-no-totals')
    return data[::-1]


def build_driver():
    def firefox():
        return webdriver.Firefox()

    def chrome():
        return webdriver.Chrome()

    def firefox_managed():
        from selenium.webdriver.firefox.service import Service as FirefoxService
        from webdriver_manager.firefox import GeckoDriverManager
        return webdriver.Firefox(service=FirefoxService(executable_path=GeckoDriverManager().install()))

    def chrome_managed():
        from selenium.webdriver.chrome.service import Service as ChromeService
        from webdriver_manager.chrome import ChromeDriverManager
        return webdriver.Chrome(service=ChromeService(executable_path=ChromeDriverManager().install()))

    plain, managed = (firefox, firefox_managed) if USE_FIREFOX else (chrome, chrome_managed)
    attempts = [managed, plain] if USE_DRIVER_MANAGER else [plain, managed]
    last_error = None
    for attempt in attempts:
        try:
            return attempt()
        except Exception as error: # fall back to the other driver strategy
            last_error = error
    raise RuntimeError(f'Could not start the browser: {last_error}')


def main():
    print('---------------------------------------------------------')
    print('                 Amazon Order Statistics')
    print('---------------------------------------------------------')
    wd = None
    try:
        years = list(range(START_YEAR, END_YEAR + 1))
        wd = build_driver()
        wd.get(f'https://{AMAZON_DOMAIN}/gp/sign-in.html')
        input('Press Enter After Login...')
        print('---------------------------------------------------------')
        all_data = []

        for year in years:
            print('Scraping', year)
            data = all_orders(wd, year)
            print(f'Scraped {len(data)} Orders in {year}')
            if not data:
                print('---------------------------------------------------------')
                continue
            year_orders = len(data)
            year_items = sum(order[1] for order in data)
            year_units = sum(order[2] for order in data)
            year_amount = sum(order[3] for order in data)
            print(f'{year} Orders:', year_orders)
            print(f'{year} Items:', year_items)
            print(f'{year} Units:', year_units)
            print(f'{year} Amount:', "{:,}".format(round(year_amount, 2)))
            year_cancelled = sum(1 for order in data if is_cancelled(order))
            if year_cancelled:
                print(f'{year} Cancelled:', year_cancelled, '(included above, amount 0)')
            print('---------------------------------------------------------')
            all_data.extend(data)
            write_csv(f'{year}.csv', data)
            write_items_csv(f'items_{year}.csv', data)
        wd.quit()
        wd = None
        if not all_data:
            print('No orders were scraped. Set DEBUG = True and re-run to save the page source.')
        else:
            write_csv('all_years.csv', all_data)
            write_items_csv('all_items.csv', all_data)
            total_orders = len(all_data)
            total_items = sum(order[1] for order in all_data)
            total_units = sum(order[2] for order in all_data)
            total_amount = sum(order[3] for order in all_data)
            print()
            print('Total Orders:', total_orders)
            print('Total Items:', total_items)
            print('Total Units:', total_units)
            print('Total Amount:', "{:,}".format(round(total_amount, 2)))
            print()
    except Exception as e:
        print('Error:', str(e))
    finally:
        if wd is not None:
            try:
                wd.quit()
            except WebDriverException:
                pass
    input('Press Enter To Exit...')


if __name__ == "__main__":
    main()
