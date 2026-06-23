import os
import csv
import requests
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GUARDIAN_API_KEY")
BASE_URL = "https://content.guardianapis.com/search"

if not API_KEY:
    raise ValueError("Missing GUARDIAN_API_KEY. Add it to your .env file.")


def fetch_nhs_news(days_back=30, page_size=10):
    """
    Fetch recent Guardian articles related to the NHS.
    """

    from_date = (date.today() - timedelta(days=days_back)).isoformat()

    params = {
        "api-key": API_KEY,
        "q": 'NHS OR "NHS England" OR "National Health Service"',
        "from-date": from_date,
        "order-by": "newest",
        "page-size": page_size,
        "show-fields": "headline,trailText,bodyText,thumbnail,shortUrl",
        "format": "json",
    }

    response = requests.get(BASE_URL, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()
    results = data.get("response", {}).get("results", [])

    articles = []

    for item in results:
        fields = item.get("fields", {})

        articles.append({
            "title": fields.get("headline", item.get("webTitle")),
            "section": item.get("sectionName"),
            "published_at": item.get("webPublicationDate"),
            "summary": fields.get("trailText"),
            "url": fields.get("shortUrl", item.get("webUrl")),
            "thumbnail": fields.get("thumbnail"),
        })

    return articles


def save_to_csv(articles, filename="nhs_guardian_news.csv"):
    if not articles:
        print("No articles found.")
        return

    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["title", "section", "published_at", "summary", "url", "thumbnail"]
        )
        writer.writeheader()
        writer.writerows(articles)

    print(f"Saved {len(articles)} articles to {filename}")


if __name__ == "__main__":
    articles = fetch_nhs_news(days_back=30, page_size=10)

    for i, article in enumerate(articles, start=1):
        print(f"\n{i}. {article['title']}")
        print(f"Section: {article['section']}")
        print(f"Published: {article['published_at']}")
        print(f"Summary: {article['summary']}")
        print(f"URL: {article['url']}")

    save_to_csv(articles)