import feedparser
from datetime import datetime, timedelta
import os
from openai import OpenAI
from dotenv import load_dotenv
from prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
import yaml
from pathlib import Path
from dateutil import parser as date_parser
from langdetect import detect
import hashlib

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
    print(f"Including articles from the last {CONFIG['rss']['days_to_include']} day(s)")
    
    articles = []
    today = datetime.now()
    cutoff_date = today.date() - timedelta(days=CONFIG['rss']['days_to_include'])
    
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            total_entries = len(feed.entries)
            entries_within_date = 0
            
            # Process all entries first
            feed_articles = []
            for entry in feed.entries:
                try:
                    # Enhanced date parsing with multiple fallbacks
                    pub_date = None
                    
                    # Try all possible date fields
                    date_fields = [
                        'published_parsed',
                        'updated_parsed',
                        'created_parsed',
                        'published',
                        'updated',
                        'created'
                    ]
                    
                    for field in date_fields:
                        if hasattr(entry, field):
                            if field.endswith('_parsed'):
                                # Handle parsed tuple format
                                try:
                                    parsed_date = getattr(entry, field)
                                    if parsed_date:
                                        pub_date = datetime(*parsed_date[:6]).date()
                                        break
                                except (TypeError, ValueError):
                                    continue
                            else:
                                # Handle string format
                                try:
                                    date_str = getattr(entry, field)
                                    if date_str:
                                        pub_date = date_parser.parse(date_str).date()
                                        break
                                except (ValueError, TypeError):
                                    continue
                    
                    # If no valid date found, use today's date and mark it
                    if pub_date is None:
                        pub_date = today.date()
                        print(f"Warning: No valid date found for article '{entry.title[:50]}...', using today's date")
                    
                    # Debug date information
                    if pub_date >= cutoff_date:
                        entries_within_date += 1
                        feed_articles.append({
                            'title': entry.title,
                            'description': getattr(entry, 'description', 
                                         getattr(entry, 'summary', 'No description available')),
                            'link': entry.link,
                            'date': pub_date.strftime(CONFIG['output']['date_format']),
                            'pub_date': pub_date  # Keep original date for debugging
                        })
                        
                except Exception as e:
                    print(f"Error processing entry: '{entry.title[:50]}...' - {str(e)}")
                    continue
            
            # Score and sort articles from this feed
            feed_articles_with_scores = [
                (article, calculate_article_priority(article))
                for article in feed_articles
            ]
            
            # Sort by score (descending) and then by date
            sorted_feed_articles = [
                article for article, score in sorted(
                    feed_articles_with_scores,
                    key=lambda x: (x[1], x[0]['pub_date']),
                    reverse=True
                )
            ]
            
            # Take top N articles from this feed based on scores
            selected_articles = sorted_feed_articles[:CONFIG['articles']['max_articles_per_feed']]
            
            print(f"Fetching from {url}:")
            print(f"  - Total entries: {total_entries}")
            print(f"  - Entries within last {CONFIG['rss']['days_to_include']} day(s): {entries_within_date}")
            print(f"  - Selected after scoring: {len(selected_articles)} (max: {CONFIG['articles']['max_articles_per_feed']})")
            
            articles.extend(selected_articles)
                    
        except Exception as e:
            print(f"Error fetching {url}: {str(e)}")
    
    print(f"\nTotal articles collected: {len(articles)}")
    print(f"Date range: {cutoff_date} to {today.date()}")
    return articles

def calculate_article_similarity(article1, article2):
    """Calculate similarity between two articles based on title similarity"""
    title1 = article1['title'].lower()
    title2 = article2['title'].lower()
    
    # If titles are exactly the same
    if title1 == title2:
        return 1.0
    
    # Calculate similarity based on words in common
    words1 = set(title1.split())
    words2 = set(title2.split())
    common_words = words1.intersection(words2)
    
    if not words1 or not words2:
        return 0.0
    
    similarity = len(common_words) / max(len(words1), len(words2))
    return similarity

def calculate_article_priority(article):
    """
    Calculate priority score for an article based on multiple factors.
    Higher score = higher priority
    """
    score = 0
    
    try:
        # Detect language
        lang = detect(article['title'])
        
        # Keywords by language
        keywords = {
            'en': {
                'critical': ['breaking', 'urgent', 'critical', 'emergency', 'alert', 'crisis',
                           'war', 'attack', 'threat', 'security', 'defense'],
                'important': ['announced', 'official', 'update', 'major', 'significant',
                            'government', 'minister', 'president', 'economy', 'military']
            },
            'pl': {
                'critical': ['pilne', 'nagłe', 'krytyczne', 'alarmujące', 'kryzys',
                           'wojna', 'atak', 'zagrożenie', 'bezpieczeństwo', 'obrona',
                           'alert', 'ostrzeżenie', 'niebezpieczeństwo'],
                'important': ['ogłoszono', 'oficjalnie', 'ważne', 'istotne', 'znaczące',
                            'rząd', 'minister', 'prezydent', 'gospodarka', 'wojsko',
                            'premier', 'sejm', 'senat']
            }
        }
        
        # Default to English if language not supported
        lang = lang if lang in keywords else 'en'
        
        title = article['title'].lower()
        description = article['description'].lower()
        
        # Check for critical keywords
        for keyword in keywords[lang]['critical']:
            if keyword in title:
                score += 5
            if keyword in description:
                score += 3
        
        # Check for important keywords
        for keyword in keywords[lang]['important']:
            if keyword in title:
                score += 3
            if keyword in description:
                score += 1
        
        # Boost score for recent articles
        try:
            article_date = datetime.strptime(article['date'], CONFIG['output']['date_format'])
            hours_old = (datetime.now() - article_date).total_seconds() / 3600
            if hours_old < 6:
                score += 4
            elif hours_old < 12:
                score += 2
            elif hours_old < 24:
                score += 1
        except:
            pass
        
    except Exception as e:
        print(f"Error calculating priority for article '{article['title'][:50]}...': {str(e)}")
    
    return score

def remove_duplicate_articles(articles, similarity_threshold=0.8):
    """Remove duplicate articles based on title similarity"""
    unique_articles = []
    
    for article in articles:
        is_duplicate = False
        for unique_article in unique_articles:
            similarity = calculate_article_similarity(article, unique_article)
            if similarity > similarity_threshold:
                is_duplicate = True
                break
        
        if not is_duplicate:
            unique_articles.append(article)
    
    return unique_articles

def summarize_with_openai(articles):
    client = OpenAI()
    
    if not articles:
        return "No news articles found for today."
    
    # Remove duplicates first
    unique_articles = remove_duplicate_articles(articles)
    
    # Score and sort articles by priority
    articles_with_scores = [
        (article, calculate_article_priority(article))
        for article in unique_articles
    ]
    
    # Sort by score (descending) and then by date
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
        lang = detect(article['title'])
        print(f"{i}. [{score}] [{lang}] {article['title']}")
    
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