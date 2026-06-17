"""
End-of-session summarisation using Claude.

Turns the raw collection of tweets gathered during a monitoring session into a
short human-readable brief: key themes, notable accounts, overall sentiment and
anything that spiked.
"""

import anthropic


class Summarizer:
    def __init__(self):
        self.client = anthropic.Anthropic()

    def summarize(self, description: str, tweets: list[dict]) -> str:
        if not tweets:
            return "No tweets were collected during this session."

        # Cap the volume sent to the model; prioritise the highest-engagement tweets.
        ranked = sorted(
            tweets,
            key=lambda t: (t.get("likes", 0) or 0) + (t.get("retweets", 0) or 0),
            reverse=True,
        )[:120]

        rendered = "\n".join(
            f"- @{t.get('author', '?')} ({t.get('likes', 0)}♥, {t.get('retweets', 0)}RT): "
            f"{(t.get('text') or '')[:280]}"
            for t in ranked
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=700,
            messages=[{
                "role": "user",
                "content": f"""You are briefing someone who has been monitoring Twitter/X.

They were watching for: "{description}"

Here are the {len(ranked)} most notable tweets collected (of {len(tweets)} total):
{rendered}

Write a concise brief (no more than ~200 words) covering:
1. Key themes and recurring topics
2. Notable accounts or voices
3. Overall sentiment
4. Anything surprising or that appears to be gaining traction

Use short paragraphs or bullet points. Do not include a preamble — start directly with the brief.""",
            }],
        )
        return response.content[0].text.strip()
