import os
from typing import Optional

import tweepy


class TwitterClient:
    def __init__(self):
        bearer_token = os.environ.get("TWITTER_BEARER_TOKEN")
        if not bearer_token:
            raise ValueError("TWITTER_BEARER_TOKEN environment variable not set")
        self.client = tweepy.Client(bearer_token=bearer_token, wait_on_rate_limit=True)

    def search_recent(
        self,
        query: str,
        max_results: int = 100,
        since_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Search recent tweets (last 7 days) matching the query.

        Returns tweets newest-first. Twitter API v2 requires max_results in [10, 100].
        Deduplication across calls is handled via since_id (pass the id of the newest
        tweet from the previous call to skip already-seen results).
        """
        kwargs: dict = {
            "query": query,
            # Clamp to the API's accepted range
            "max_results": max(10, min(max_results, 100)),
            "tweet_fields": ["created_at", "public_metrics", "author_id", "text"],
            "expansions": ["author_id"],
            "user_fields": ["username", "name"],
        }
        if since_id:
            kwargs["since_id"] = since_id

        try:
            response = self.client.search_recent_tweets(**kwargs)
        except tweepy.TweepyException as e:
            raise RuntimeError(f"Twitter API error: {e}") from e

        if not response.data:
            return []

        # Build user lookup from the expanded includes
        users: dict = {}
        if response.includes and "users" in response.includes:
            for user in response.includes["users"]:
                users[user.id] = user

        tweets = []
        for tweet in response.data:
            author = users.get(tweet.author_id)
            metrics = tweet.public_metrics or {}
            username = author.username if author else "unknown"
            tweets.append({
                "id": str(tweet.id),
                "text": tweet.text,
                "author_id": str(tweet.author_id),
                "author": username,
                "author_name": author.name if author else "unknown",
                "created_at": tweet.created_at.isoformat() if tweet.created_at else None,
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "url": f"https://x.com/{username}/status/{tweet.id}",
            })

        return tweets
