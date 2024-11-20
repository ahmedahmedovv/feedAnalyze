import feedparser
from datetime import datetime
import os
from openai import OpenAI
from dotenv import load_dotenv
from prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
import yaml
from pathlib import Path

# Load environment variables
load_dotenv()

def load_config():
    config_path = Path(__file__).parent / 'config.yaml'
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

# Load config at module level
CONFIG = load_config()

def fetch_rss_feeds():
    # Read RSS links from file without limiting the number of feeds
    with open('rss_links.txt', 'r') as file:
        rss_urls = [line.strip() for line in file if line.strip()]
    
    print(f"\nProcessing {len(rss_urls)} feeds")
    
    articles = []
    today = datetime.now().date()
    
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            total_entries = len(feed.entries)
            # Limit entries per feed
            entries = feed.entries[:CONFIG['articles']['max_articles_per_feed']]
            
            print(f"Fetching from {url}: Found {total_entries} entries, processing {len(entries)} (max: {CONFIG['articles']['max_articles_per_feed']})")
            
            for entry in entries:
                try:
                    # Try different date fields that might be present in the feed
                    if hasattr(entry, 'published_parsed'):
                        pub_date = datetime(*entry.published_parsed[:6]).date()
                    elif hasattr(entry, 'updated_parsed'):
                        pub_date = datetime(*entry.updated_parsed[:6]).date()
                    else:
                        pub_date = today
                    
                    # Use configured days_to_include
                    if (today - pub_date).days <= CONFIG['rss']['days_to_include']:
                        articles.append({
                            'title': entry.title,
                            'description': getattr(entry, 'description', 
                                         getattr(entry, 'summary', 'No description available')),
                            'link': entry.link,
                            'date': pub_date.strftime(CONFIG['output']['date_format'])
                        })
                        
                except Exception as e:
                    print(f"Error processing entry date: {str(e)}")
                    continue
                    
        except Exception as e:
            print(f"Error fetching {url}: {str(e)}")
    
    print(f"Total articles collected: {len(articles)}")
    return articles

def calculate_article_priority(article):
    """
    Calculate priority score for an article based on multiple factors.
    Higher score = higher priority
    """
    score = 0
    
    # Priority keywords in title or description (customize these lists based on your needs)
    critical_keywords = ['breaking', 'urgent', 'critical', 'emergency', 'alert', 'crisis']
    important_keywords = ['announced', 'official', 'update', 'major', 'significant']
    
    title = article['title'].lower()
    description = article['description'].lower()
    
    # Check for critical keywords (higher weight)
    for keyword in critical_keywords:
        if keyword in title:
            score += 5  # Higher score for critical keywords in title
        if keyword in description:
            score += 3  # Lower score for critical keywords in description
    
    # Check for important keywords (lower weight)
    for keyword in important_keywords:
        if keyword in title:
            score += 3  # Higher score for important keywords in title
        if keyword in description:
            score += 1  # Lower score for important keywords in description
    
    # Boost score for recent articles
    try:
        article_date = datetime.strptime(article['date'], CONFIG['output']['date_format'])
        hours_old = (datetime.now() - article_date).total_seconds() / 3600
        if hours_old < 6:  # Extra points for very recent news
            score += 4
        elif hours_old < 12:
            score += 2
        elif hours_old < 24:
            score += 1
    except:
        pass  # If date parsing fails, no time bonus
    
    return score

def summarize_with_openai(articles):
    client = OpenAI()
    
    if not articles:
        return "No news articles found for today."
    
    # Score and sort articles by priority
    articles_with_scores = [
        (article, calculate_article_priority(article))
        for article in articles
    ]
    
    # Sort by score (descending) and then by date (newest first) for tiebreakers
    sorted_articles = [
        article for article, score in sorted(
            articles_with_scores,
            key=lambda x: (x[1], x[0]['date']),
            reverse=True
        )
    ]
    
    # Take top N articles based on config
    articles_to_process = sorted_articles[:CONFIG['articles']['max_articles_to_process']]
    
    # Debug info to see selection process
    print("\nSelected articles for processing:")
    for i, article in enumerate(articles_to_process, 1):
        score = next(score for a, score in articles_with_scores if a == article)
        print(f"{i}. [{score}] {article['title']}")
    
    articles_content = ""
    for article in articles_to_process:
        description = article['description']
        if len(description) > CONFIG['rss']['max_description_length']:
            description = description[:CONFIG['rss']['max_description_length']] + "..."
            
        articles_content += f"Title: {article['title']}\n"
        articles_content += f"Source: {article.get('source', extract_source_from_url(article['link']))}\n"
        articles_content += f"Date: {article['date']}\n"
        articles_content += f"Description: {description}\n"
        articles_content += f"Link: {article['link']}\n\n"

    user_prompt = USER_PROMPT_TEMPLATE.format(articles=articles_content)
    modified_system_prompt = SYSTEM_PROMPT.format(max_news_items=CONFIG['openai']['max_news_items'])

    try:
        response = client.chat.completions.create(
            model=CONFIG['openai']['model'],
            messages=[
                {"role": "system", "content": modified_system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=CONFIG['openai']['max_tokens'],
            temperature=CONFIG['openai']['temperature']
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error during OpenAI API call: {str(e)}")
        # If we hit token limit, try with fewer articles
        if "context length" in str(e).lower():
            try:
                # Try again with half the articles
                half_content = "\n\n".join(articles_content.split("\n\n")[:CONFIG['articles']['max_articles_to_process']//2])
                user_prompt = USER_PROMPT_TEMPLATE.format(articles=half_content)
                response = client.chat.completions.create(
                    model=CONFIG['openai']['model'],
                    messages=[
                        {"role": "system", "content": modified_system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    max_tokens=CONFIG['openai']['max_tokens'],
                    temperature=CONFIG['openai']['temperature']
                )
                return response.choices[0].message.content
            except Exception as e2:
                print(f"Error during second attempt: {str(e2)}")
                return "Error generating summary. Please try again."
        return "Error generating summary. Please try again."

def extract_source_from_url(url):
    """Extract source name from URL"""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        # Remove www. and .com/.org/etc
        source = domain.replace('www.', '').split('.')[0]
        return source.capitalize()
    except:
        return "Unknown Source"

def save_report(summary):
    os.makedirs(CONFIG['output']['reports_directory'], exist_ok=True)
    
    filename = f"{CONFIG['output']['reports_directory']}/news_summary_{datetime.now().strftime(CONFIG['output']['date_format'])}.txt"
    
    header = f"Daily News Summary - {datetime.now().strftime(CONFIG['output']['date_format'])}\n"
    header += "Note: Links in square brackets [] are clickable in most text editors.\n\n"
    
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(header)
        file.write(summary)

def main():
    # Fetch articles from RSS feeds
    articles = fetch_rss_feeds()
    
    # Generate summary using OpenAI
    summary = summarize_with_openai(articles)
    
    # Save the report
    save_report(summary)
    
    print(f"News summary has been generated and saved to the reports folder.")

if __name__ == "__main__":
    main() 