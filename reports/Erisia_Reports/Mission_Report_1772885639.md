### Web Scraping using BeautifulSoup and Scrapy
#### Overview
This mission involves researching how to programmatically scrape website source code using BeautifulSoup and Scrapy in Python. We will create a tool that can scrape a website and extract relevant information.

#### TOOL_SCHEMA
```python
TOOL_SCHEMA = {
    "name": "Web Scraper",
    "description": "A tool for scraping website source code using BeautifulSoup and Scrapy",
    "params": {
        "url": {"type": "string", "required": True},
        "parser": {"type": "string", "required": False, "default": "html.parser"}
    }
}
```

#### execute_skill Function
```python
import requests
from bs4 import BeautifulSoup

def execute_skill(**kwargs):
    """
    Execute the web scraping skill.

    Args:
    - url (str): The URL of the website to scrape.
    - parser (str): The parser to use for parsing the HTML content. Defaults to "html.parser".

    Returns:
    - soup (BeautifulSoup): The parsed HTML content of the website.
    """
    url = kwargs.get("url")
    parser = kwargs.get("parser", "html.parser")

    # Send an HTTP request to the URL
    response = requests.get(url)

    # Check if the request is successful
    if response.status_code == 200:
        # Parse the HTML content of the page using the BeautifulSoup library
        soup = BeautifulSoup(response.text, parser)

        return soup
    else:
        print(f"Failed to retrieve the webpage. Status code: {response.status_code}")
        return None
```

### Example Usage
```python
url = "https://www.example.com"
soup = execute_skill(url=url)

if soup:
    # Find all story titles
    titles = soup.find_all('span', class_='titleline')
    for title in titles:
        print(title.text)
```

### Scrapy Example
For larger scale web scraping, we can use Scrapy. Here's an example of how to use Scrapy to scrape a website:
```python
import scrapy

class WebScraper(scrapy.Spider):
    name = "web_scraper"
    start_urls = [
        'https://www.example.com',
    ]

    def parse(self, response):
        # Find all story titles
        titles = response.css('span.titleline::text').get()
        yield {
            'title': titles,
        }
```
To run the Scrapy spider, save this code in a file named `web_scraper.py` and run it using the command `scrapy runspider web_scraper.py`.

Note: This is a basic example and may need to be modified based on the specific website being scraped. Additionally, be sure to check the website's terms of use and robots.txt file to ensure that web scraping is allowed.