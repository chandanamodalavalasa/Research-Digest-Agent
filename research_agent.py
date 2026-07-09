import os
import requests
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import Counter
from bs4 import BeautifulSoup
from datetime import date
from dotenv import load_dotenv

load_dotenv()

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
# common words to ignore when scoring - they don't carry meaning
STOPWORDS = set("""
a an the this that these those is are was were be been being have has had
do does did will would shall should may might must can could of in on at
to for with as by from and or but not no nor so yet it its it's their they
he she his her him we our us you your i my me if than then there here
""".split())


def search_web(topic, num_results=5):
    """Search Google via SerpAPI and return a list of top results.
    Retries once if the connection is slow/flaky."""
    params = {
        "engine": "google",
        "q": topic,
        "num": num_results,
        "api_key": SERPAPI_KEY,
    }

    for attempt in (1, 2):
        try:
            resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.RequestException as e:
            print(f"  search attempt {attempt} failed: {e}")
            if attempt == 2:
                return []  # give up gracefully, don't crash the whole run

    results = []
    for item in data.get("organic_results", [])[:num_results]:
        results.append({
            "title": item.get("title", "Untitled"),
            "url": item.get("link"),
            "snippet": item.get("snippet", ""),
        })

    return results

def fetch_article_text(url):
    """Download a page and pull out the readable paragraph text.
    Some sites block scrapers or need JS to render - if that happens
    we just return None and fall back to the snippet later."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DigestBot/1.0)"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  (couldn't fetch this one: {e})")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
    text = "\n".join(p for p in paragraphs if len(p) > 40)

    return text[:4000] if text else None

def summarize_topic(topic, articles, sentences_per_topic=5):
    """Pick the most 'important' sentences out of all the scraped articles,
    based on word frequency. No API, no cost, runs fully offline."""

    full_text = ""
    sources_used = []
    for i, art in enumerate(articles, 1):
        body = art["text"] or art["snippet"]
        if body:
            full_text += body + " "
            sources_used.append(i)

    if not full_text.strip():
        return "No readable content was found for this topic today."

    # split into sentences (simple rule: split on . ! ? followed by a space/capital)
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30]

    if not sentences:
        return "Not enough sentence-length content to summarize."

    # score each word by how often it appears (ignoring stopwords)
    words = re.findall(r'\b[a-zA-Z]{3,}\b', full_text.lower())
    word_freq = Counter(w for w in words if w not in STOPWORDS)

    # score each sentence by summing the frequency of its words
    sentence_scores = {}
    for s in sentences:
        s_words = re.findall(r'\b[a-zA-Z]{3,}\b', s.lower())
        score = sum(word_freq.get(w, 0) for w in s_words)
        sentence_scores[s] = score / (len(s_words) + 1)  # normalize by length

    # take the top-scoring sentences, but keep their original order
    top_sentences = sorted(sentence_scores, key=sentence_scores.get, reverse=True)
    top_sentences = top_sentences[:sentences_per_topic]
    top_sentences.sort(key=lambda s: sentences.index(s))

    summary = " ".join(top_sentences)
    summary += f"\n\nSources: {', '.join(str(i) for i in sources_used)}"
    return summary

def load_topics():
    with open("topics.txt", "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def build_digest(topic_summaries):
    today = date.today().strftime("%B %d, %Y")
    lines = [f"DAILY RESEARCH DIGEST - {today}", "=" * 50, ""]

    for topic, summary in topic_summaries:
        lines.append(f"## {topic}")
        lines.append(summary)
        lines.append("")
        lines.append("-" * 50)
        lines.append("")

    return "\n".join(lines)


def save_digest(digest_text):
    filename = f"digests/digest_{date.today().isoformat()}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(digest_text)
    print(f"\nSaved to {filename}")

def email_digest(digest_text):
    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD]):
        print("Email settings incomplete in .env - skipping email (digest was still saved to disk)")
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = f"Research Digest - {date.today().strftime('%b %d, %Y')}"
    msg.attach(MIMEText(digest_text, "plain"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"Digest emailed to {EMAIL_TO}")
    except smtplib.SMTPException as e:
        print(f"Email failed to send: {e}")

if __name__ == "__main__":
    topics = load_topics()
    topic_summaries = []

    for topic in topics:
        print(f"\n=== Researching: {topic} ===")
        results = search_web(topic)

        articles = []
        for r in results:
            print(f"Fetching: {r['title']}")
            text = fetch_article_text(r["url"])
            articles.append({**r, "text": text})

        summary = summarize_topic(topic, articles)
        topic_summaries.append((topic, summary))

    digest = build_digest(topic_summaries)
    save_digest(digest)
    email_digest(digest)
    print("\nDone. Full digest:\n")
    print(digest)