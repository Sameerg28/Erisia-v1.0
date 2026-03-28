import requests
import json

def execute_skill(**kwargs):
    url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    response = requests.get(url)
    topStories = response.json()
    headlines = []
    for story_id in topStories[:5]:
        story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
        story_response = requests.get(story_url)
        story = story_response.json()
        headlines.append(story['title'])
    return headlines

tool_schema = {
    "type": "function",
    "function": {
        "name": "hacker_news_headlines.py",
        "description": "Fetches the top 5 current news headlines from Hacker News using their public API.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
}