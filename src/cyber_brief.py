import asyncio
import edge_tts
import feedparser
import logging
import os
import re
import sys
import smtplib
import time
from pydub import AudioSegment
from pydub.effects import compress_dynamic_range
from datetime import datetime
import xml.etree.ElementTree as ET
from email.utils import formatdate
from email.mime.text import MIMEText
from pydub.effects import normalize
from pathlib import Path

# Setup logging so if the script fails we know why.
logging.basicConfig(
    filename="briefing.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Define the feeds for our news, current top 3 with RSS feeds.
SOURCES = {
    "Dark Reading": "https://www.darkreading.com/rss.xml",
    "The Hacker News": "https://feeds.feedburner.com/TheHackersNews",
    "Krebs on Security": "https://krebsonsecurity.com/feed/"
}

# Grab the top 3 headlines from the RSS feed of each source.
def get_top_headlines():
    all_articles = {}
    
    for name, url in SOURCES.items():
        feed = feedparser.parse(url)
        if feed.bozo:
            logging.warning(f"Malformed RSS feed from {name}: {feed.bozo_exception}")
        top_three = feed.entries[:3] # we use [:3] to grab the first 3 entries.

        articles = []
        for entry in top_three:
            clean_summary = re.sub('<.*?>', '', getattr(entry, "summary", "")) # Safety net if less than 3 articles.
            articles.append({
                "title": entry.title,
                "link": entry.link,
                "summary": clean_summary
            })

        all_articles[name] = articles
    logging.info("RSS feeds parsed successfully.")
    return all_articles

# Function to send out the email through SMTP.
def send_email(content):
    sender =os.getenv("BRIEFING_EMAIL")
    password = os.getenv("BRIEFING_PASSWORD")
    recipient = sender

    if not sender or not password:
        logging.error("Email credentials not set in environment.")
        raise RunTimeError("Missing email credentials")

    msg = MIMEText(content, "html")
    msg["Subject"] = f"Cybersecurity Briefing - {datetime.now().strftime('%B %d, %Y')}"
    msg["From"] = sender
    msg["To"] = recipient

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.send_message(msg)
    logging.info("E-Mail sent successfully.")

# Function to format the briefing email.
def format_email(articles, filename):
    html = "<h1>Morning Cybersecurity Briefing</h1>"

    # Very basic Header and List style links to the articles.
    for source, items in articles.items():
        html += f"<h2>{source}</h2><ul>"
        for item in items:
            html += f'<li><a href="{item["link"]}">{item["title"]}</a></li>'
        html += "</ul>"
    html += f'<p><a href="https://rathserver.org/podcast/{filename}">Listen to Podcast</a></p>'

    return html

def update_podcast_rss(filename):
    OUTPUT_DIR = Path("/var/www/html/podcast")
    rss_path = OUTPUT_DIR / "feed.xml"
    
    episode_url = f"https://rathserver.org/podcast/{filename}"
    episode_path = OUTPUT_DIR / filename
    
    file_size = os.path.getsize(episode_path)
    pub_date = formatdate(localtime=True)
    
    # If RSS exists, load it
    if rss_path.exists():
        tree = ET.parse(rss_path)
        root = tree.getroot()
        channel = root.find("channel")
    else:
        # Create new RSS structure
        rss = ET.Element("rss", version="2.0")
        channel = ET.SubElement(rss, "channel")
        
        ET.SubElement(channel, "title").text = "Cybersecurity Briefing"
        ET.SubElement(channel, "link").text = "https://rathserver.org/podcast/"
        ET.SubElement(channel, "description").text = "Daily cybersecurity and technology news briefing."
        ET.SubElement(channel, "language").text = "en-us"
        
        root = rss
        tree = ET.ElementTree(root)
    
    # Create episode item
    item = ET.Element("item")
    
    ET.SubElement(item, "title").text = f"Cybersecurity Briefing - {datetime.now().strftime('%B %d, %Y')}"
    ET.SubElement(item, "pubDate").text = pub_date
    guid = ET.SubElement(item, "guid")
    guid.text = episode_url
    guid.set("isPermaLink", "true")
    
    enclosure = ET.SubElement(item, "enclosure")
    enclosure.set("url", episode_url)
    enclosure.set("length", str(file_size))
    enclosure.set("type", "audio/mpeg")
    
    # Ensure that the item does not already exist
    existing_items = channel.findall("item")
    for existing in existing_items:
        guid = existing.find("guid")
        if guid is not None and guid.text == episode_url:
            logging.info("Episode already exists in RSS feed. Skipping insert.")
            return
            
    # Insert new episode at top
    channel.insert(0, item)
    
    # Optional: Keep only last 30 episodes
    items = channel.findall("item")
    if len(items) > 30:
        for old_item in items[30:]:
            channel.remove(old_item)
    
    tree.write(rss_path, encoding="utf-8", xml_declaration=True)
    
    logging.info("Podcast RSS updated successfully.")

# Function to create a podcast script using the gathered articles, trying to stay professional and clean.
def create_podcast_script(articles):
    script = f"Good morning, today is {datetime.now().strftime('%A, %B %d, %Y')} and welcome to your Cybersecurity and Technology Briefing. Here are today’s top stories."

    for source, items in articles.items():
        script += f"\nFrom {source}:\n"
        for item in items:
            script += (
                f"\nHeadline: {item['title']}. \n"
                f"Here’s the summary. {item['summary']}.\n\n"
            )
            
    script += """
    That concludes today’s briefing.
    Stay secure, stay informed, and we’ll see you next time.
    Text-to-speech provided by EDGE TTS voice: United Kingdom Sonia
    Intro and Outro Music by transistor.fm
    Theme music by nihilore.com
    """

    return script
    
# Function to generate our narrators voice, uses OpenAI TTS and can take a while especially if there is heavy traffic.
async def generate_audio(script):
    communicate = edge_tts.Communicate(script, voice="en-GB-SoniaNeural")
    await communicate.save("narration.mp3")
    logging.info("Narration created successfully.")
    
# Function to mix audio for final mix.
def mix_audio(filename):
    # pydub uses milliseconds so 1 second = 1000 milliseconds [10000 milliseconds = 10 second intro/outro]
    # Load audio.
    voice = AudioSegment.from_file("narration.mp3")
    intro = AudioSegment.from_file("intro.mp3")[:10000] - 6 # Only takes the first 10 seconds, and decreases the volume by 6.
    outro = AudioSegment.from_file("outro.mp3")[:10000] - 6
    intro = intro.fade_out(2000) # Add some nice touches to fade in and out.
    outro = outro.fade_in(2000)
    bed = AudioSegment.from_file("haven.mp3") - 25 # Reduce the volume to be background to the narrator, can take some messing around to get it just right.

    # Loop bed music to match narration length.
    bed_looped = bed * (len(voice) // len(bed) + 1)
    bed_looped = bed_looped[:len(voice)]

    # Overlay bed under voice.
    voice_with_bed = voice.overlay(bed_looped)

    # Assemble final podcast with intro and outro.
    final = intro + voice_with_bed + outro
    
    # Export to our webserver folder.
    output_path = Path("/var/www/html/podcast/")
    output_path.mkdir(parents=True, exist_ok=True)
    
    final.export(output_path / filename, format="mp3", bitrate="192k")
    os.remove("narration.mp3") # Delete the temporary narration file.
    logging.info("Audio mixed successfully.")

# MAIN - Do all the things.
def main():
    start_time = time.perf_counter()
    # Filename: year-month-day_cyber_brief.mp3
    filename = datetime.now().strftime("%Y-%m-%d") + "_cyber_brief.mp3"
    
    # Grab the headlines and assign them to variables.
    articles = None
    for attempt in range(3):
        try:
            articles = get_top_headlines()
            break
        except Exception:
            logging.warning(f"Attempt {attempt + 1}/3 failed, Retrying RSS Feed...")
            time.sleep(5)
    
    if not articles:
        logging.error("Critical RSS Feed error")
        sys.exit()
    
    script = create_podcast_script(articles)
    
    
    # Run this first to get the narrator audio from edge TTS.
    tts_success = False
    for attempt in range(3):
        try:
            asyncio.run(generate_audio(script))
            tts_success = True
            break
        except Exception:
            logging.warning(f"Attempt {attempt + 1}/3 failed, Retrying edge TTS generation...")
            time.sleep(5)
    
    if not tts_success:
        logging.error("TTS generation failed after 3 attempts.")
        sys.exit()
    
    # Then we mix everything and export it.
    mix_audio(filename)
    
    #Podcast RSS Feed
    for attempt in range(3):
        try:
            update_podcast_rss(filename)
            break
        except Exception:
            logging.warning(f"Attempt {attempt + 1}/3 failed, Retrying RSS Feed update")
            time.sleep(5)
    
    # Email section.
    mail_content = format_email(articles, filename)
    for attempt in range(3):
        try:
            send_email(mail_content)
            break
        except Exception:
            logging.warning(f"Attempt {attempt + 1}/3 failed, Retrying email...")
            time.sleep(5)
            
    
    end_time = time.perf_counter()
    elapsed_time = end_time - start_time
    logging.info(f"Elapsed time: {elapsed_time:.4f} seconds")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Fatal error in main execution")
