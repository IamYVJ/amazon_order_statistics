
# Amazon Order Statistics

Know how many orders you have made and how much you have spent on Amazon!

## Variables

- `USE_FIREFOX` 
Default Value: _TRUE_ 
To be changed to _FALSE_ to use Chrome
- `USE_DRIVER_MANAGER` 
Default Value: _FALSE_ 
Selenium 4.6+ downloads the matching driver by itself. Change to _TRUE_ to download it with `webdriver-manager` instead. Either way, the script falls back to the other method if the first one fails.
- `AMAZON_DOMAIN` 
Default Value: _www.amazon.in_ 
Change for other marketplaces, e.g. _www.amazon.com_
- `START_YEAR` 
Default Value: _2014_ 
First year to be scraped
- `END_YEAR` 
Default Value: _current year_ 
Last year to be scraped
- `DEBUG` 
Default Value: _FALSE_ 
Change to _TRUE_ to save the page source as `debug-<year>-*.html` when a page cannot be parsed
## Steps To Run

### Windows Terminal

- `git clone https://github.com/IamYVJ/amazon_order_statisics.git`
- `cd amazon_order_statisics`
- `pip3 install -r requirements.txt`
- `python3 scraper.py`

Sign in to Amazon in the browser window the script opens, then press Enter in the terminal. A CSV is written per year plus `all_years.csv`.

## Output

| Column | Meaning |
| --- | --- |
| `Date` | Date the order was placed (or charged, for subscriptions) |
| `Number` | Distinct items in the order |
| `Units` | Total quantity, counting the per-item quantity badges |
| `Amount` | Order total. Cancelled orders show 0, because Amazon prints no total for them |
| `Details` | Order number. Physical orders look like `402-1234567-1234567`, digital ones `D01-1234567-1234567` |
| `Name` | Item names, separated by ` \| ` |
| `Status` | Delivery status, e.g. `Delivered 3 March`, `Cancelled`, `Returned`. Filter on `Cancelled` to exclude those orders |

## Requirements

The script is written in Python 3

Firefox or Chrome should be installed on the system

## Notes

Amazon reworked the order history pages after 2022: the listing moved from
`/gp/your-account/order-history?orderFilter=year-N` to `/your-orders/orders?timeFilter=year-N`,
and the order cards no longer use the old `a-color-secondary value` markup. The scraper now reads
each card by its on-screen labels ("Order placed", "Total", "Order #") and falls back to the older
selectors, so it handles both layouts. Grocery orders (Amazon Fresh / Amazon Now) show their
items as image-only tiles with no product title, so their names are read from the thumbnail
`alt` text, and per-item quantities come from the `product-image__qty` badge. If Amazon changes the pages again and nothing is scraped,
set `DEBUG = True` and re-run to capture the page source.
