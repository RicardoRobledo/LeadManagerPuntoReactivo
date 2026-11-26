import requests

from django.conf import settings


def web_search_tool(query: str, num_results: int = 3,) -> list:
    """
    Perform a web search using the specified search engine.

    Args:
        query (str): The search query.
        num_results (int): The number of results to return. Default is 3.

    Returns:
        list: A list of search results, each represented as a dictionary with 'title' and 'link'.
    """

    url = "https://api.tavily.com/search"

    headers = {
        "Content-Type": "application/json"
    }

    payload = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "include_answer": True,
        "include_raw_content": False,
        "max_results": num_results
    }

    result = requests.post(url, headers=headers, json=payload)
    result.raise_for_status()

    return result.json()
