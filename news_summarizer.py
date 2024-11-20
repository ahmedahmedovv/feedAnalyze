import feedparser
from datetime import datetime
import os
from openai import OpenAI
from dotenv import load_dotenv
from prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

# Load environment variables
load_dotenv()

def fetch_rss_feeds():
    # Read RSS links from file
    with open('rss_links.txt', 'r') as file:
        rss_urls = [line.strip() for line in file if line.strip()]
    
    articles = []
    today = datetime.now().date()
    
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            print(f"Fetching from {url}: Found {len(feed.entries)} entries")
            
            for entry in feed.entries:
                try:
                    # Try different date fields that might be present in the feed
                    if hasattr(entry, 'published_parsed'):
                        pub_date = datetime(*entry.published_parsed[:6]).date()
                    elif hasattr(entry, 'updated_parsed'):
                        pub_date = datetime(*entry.updated_parsed[:6]).date()
                    else:
                        # If no date parsing works, assume it's recent and include it
                        pub_date = today
                    
                    # Be more lenient with date matching - include articles from last 24 hours
                    if (today - pub_date).days <= 1:
                        articles.append({
                            'title': entry.title,
                            'description': getattr(entry, 'description', 
                                         getattr(entry, 'summary', 'No description available')),
                            'link': entry.link,
                            'date': pub_date.strftime('%Y-%m-%d')
                        })
                        
                except Exception as e:
                    print(f"Error processing entry date: {str(e)}")
                    continue
                    
        except Exception as e:
            print(f"Error fetching {url}: {str(e)}")
    
    print(f"Total articles collected: {len(articles)}")
    return articles

def summarize_with_openai(articles, max_news=20):
    client = OpenAI()
    
    if not articles:
        return "No news articles found for today."
    
    # Sort articles by date (newest first)
    sorted_articles = sorted(articles, key=lambda x: x['date'], reverse=True)
    
    # Limit the number of articles to process (reduce token count)
    max_articles_to_process = 100  # Adjust this number if needed
    articles_to_process = sorted_articles[:max_articles_to_process]
    
    # Prepare content for summarization
    articles_content = ""
    for article in articles_to_process:
        # Truncate description more aggressively
        description = article['description']
        if len(description) > 300:  # Reduced from 500 to 300
            description = description[:300] + "..."
            
        articles_content += f"Title: {article['title']}\n"
        articles_content += f"Source: {article.get('source', extract_source_from_url(article['link']))}\n"
        articles_content += f"Date: {article['date']}\n"
        articles_content += f"Description: {description}\n"
        articles_content += f"Link: {article['link']}\n\n"

    # Format the user prompt with the articles content
    user_prompt = USER_PROMPT_TEMPLATE.format(articles=articles_content)

    # Update the system prompt
    modified_system_prompt = SYSTEM_PROMPT + f"\nImportant: Select and summarize EXACTLY {max_news} most important news items, prioritizing by category (security/defense, political, economic, technology, other)."

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": modified_system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=4000  # Limit the response size
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error during OpenAI API call: {str(e)}")
        # If we hit token limit, try with fewer articles
        if "context length" in str(e).lower():
            try:
                # Try again with half the articles
                half_content = "\n\n".join(articles_content.split("\n\n")[:max_articles_to_process//2])
                user_prompt = USER_PROMPT_TEMPLATE.format(articles=half_content)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": modified_system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    max_tokens=4000
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
    # Create reports directory if it doesn't exist
    os.makedirs('reports', exist_ok=True)
    
    # Generate filename with current date
    filename = f"reports/news_summary_{datetime.now().strftime('%Y-%m-%d')}.txt"
    
    # Add a note about clickable links at the top of the file
    header = f"Daily News Summary - {datetime.now().strftime('%Y-%m-%d')}\n"
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