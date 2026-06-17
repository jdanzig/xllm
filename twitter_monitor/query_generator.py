import json

import anthropic


class QueryGenerator:
    def __init__(self):
        self.client = anthropic.Anthropic()

    def generate_search_query(self, description: str) -> tuple[str, str]:
        """
        Use Claude to convert an English description into an effective Twitter search query.

        Returns (query_string, explanation).
        Note: lang: and -is:retweet are NOT included here; they are appended by the caller.
        """
        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"""Convert this description of what to monitor on Twitter/X into an effective search query.

Description: {description}

Available Twitter search operators:
- Quoted phrases: "exact phrase"
- Boolean: word1 OR word2, word1 word2 (implicit AND)
- Exclude: -word or -"phrase"
- Hashtags: #topic
- Cashtags: $TICKER
- From/to: from:username, to:username
- Engagement: min_faves:N, min_retweets:N
- Has media: has:images, has:videos, has:links

Rules:
- Keep the query focused. Prefer specificity over breadth.
- Do NOT include lang: or -is:retweet (added automatically).
- Do NOT include quotes around the whole query.

Respond with ONLY a JSON object (no markdown fences):
{{"query": "<the search query>", "explanation": "<one sentence explaining what this will find>"}}""",
            }],
        )

        raw = response.content[0].text.strip()
        # Strip markdown code fences if Claude adds them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        return result["query"], result["explanation"]

    def filter_tweets(self, description: str, tweets: list[dict]) -> list[dict]:
        """
        Use Claude (Haiku for cost efficiency) to filter a list of tweets for relevance
        to the original description.

        Returns only the tweets Claude considers relevant.
        """
        if not tweets:
            return []

        numbered = "\n".join(
            f"{i + 1}. @{t['author']}: {t['text'][:300]}"
            for i, t in enumerate(tweets)
        )

        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"""You are filtering tweets for relevance.

The user wants to monitor: "{description}"

Tweets to evaluate:
{numbered}

Return a JSON array containing the 1-based indices of tweets that are GENUINELY relevant.
Be selective — only include tweets that clearly match the intent described above.
Example output: [1, 3, 5]

Output ONLY the JSON array, nothing else.""",
            }],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        relevant_indices: list[int] = json.loads(raw)
        return [tweets[i - 1] for i in relevant_indices if 1 <= i <= len(tweets)]

    def refine_query(
        self,
        description: str,
        current_query: str,
        sample_tweets: list[dict],
        feedback: str = "",
    ) -> tuple[str, str]:
        """
        Given the current query and a sample of the tweets it returned, produce an
        improved query. Used by the post-first-check refinement loop.

        `feedback` is optional free-text from the user describing what was wrong
        (e.g. "too much sports news, I only care about the company").

        Returns (new_query, explanation).
        """
        sample = "\n".join(
            f"- @{t['author']}: {t['text'][:200]}"
            for t in sample_tweets[:15]
        ) or "(no tweets were returned)"

        feedback_block = (
            f"\nThe user gave this feedback about the results:\n{feedback}\n"
            if feedback.strip() else ""
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"""You previously built a Twitter/X search query for this goal.

Goal: {description}

Current query: {current_query}

A sample of the tweets it returned:
{sample}
{feedback_block}
Produce an improved query that better matches the goal — tighten it if the
results are noisy/off-topic, or broaden it if too few relevant results came back.

Available operators: "exact phrase", OR, implicit AND, -exclude, #hashtag,
$CASHTAG, from:user, to:user, min_faves:N, min_retweets:N, has:images,
has:videos, has:links.

Rules:
- Do NOT include lang: or -is:retweet (added automatically).
- Do NOT wrap the whole query in quotes.

Respond with ONLY a JSON object (no markdown fences):
{{"query": "<improved query>", "explanation": "<one sentence on what changed>"}}""",
            }],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        return result["query"], result["explanation"]
