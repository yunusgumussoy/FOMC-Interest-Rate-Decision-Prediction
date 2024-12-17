# -*- coding: utf-8 -*-
"""
Created on Mon Dec 16 12:52:39 2024

@author: Yunus
"""

import os
import re
import logging
from datetime import datetime
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dateutil import parser

BASE_URL = "https://www.federalreserve.gov"
MONETARY_POLICY_URL = "/monetarypolicy/fomccalendars.htm"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/100.0.4896.127 Safari/537.36"
}

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def tag_has_statement(tag):
    return tag.name == "div" and "Statement:" in tag.text

def tag_has_minutes(tag):
    return tag.name == "div" and "Minutes:" in tag.text

def format_date(date):
    return date.strftime("%Y-%m-%d")
        
def read_most_recent_date(file_path):
    """Reads the most recent communication date from the specified file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            most_recent_date_string = f.read().strip()
            return parser.parse(most_recent_date_string)
    except FileNotFoundError:
        logging.warning(f"File {file_path} not found. Defaulting to earliest possible date.")
        return parser.parse("1900-01-01")
    except Exception as e:
        logging.error(f"Error reading most recent date file: {e}")
        return parser.parse("1900-01-01")

def write_most_recent_date(file_path, date):
    """Writes the most recent communication date to the specified file."""
    try:
        if isinstance(date, str):  # Convert string to datetime if necessary
            date = parser.parse(date)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(date.strftime("%Y-%m-%d"))
    except Exception as e:
        logging.error(f"Error writing to {file_path}: {e}")

def fetch_page(url, headers):
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logging.error(f"Error fetching {url}: {e}")
        return None

def parse_communication_page(html_content, doc_type):
    try:
        doc = BeautifulSoup(html_content, features="html5lib")
        if doc_type == "Statement":
            comm_text = doc.find("div", id="article").find_all("div")[2].text.strip()
        else:
            comm_text = doc.find("div", id="article").text.strip()
        return comm_text
    except Exception as e:
        logging.error(f"Error parsing {doc_type} page: {e}")
        return ""

def parse_fomc_page(html_content):
    doc = BeautifulSoup(html_content, features="html5lib")
    panels = doc.find_all("div", {"class": "panel panel-default"})
    return panels

def extract_year_from_panel(panel):
    try:
        panel_title = panel.find("div", {"class": "panel-heading"}).text
        numbers_in_title = re.findall(r"\d+", panel_title)
        return numbers_in_title[-1]
    except Exception as e:
        logging.error(f"Error extracting year from panel: {e}")
        return ""

def assemble_meeting_timestamp(row, year):
    try:
        month_text = row.find("div", {"class": "fomc-meeting__month"}).text
        month = month_text.split("/")[-1] if "/" in month_text else month_text
        date_text = row.find("div", {"class": "fomc-meeting__date"}).text
        date = re.findall(r"\d+", date_text)[-1]
        return parser.parse(f"{year} {month} {date}")
    except Exception as e:
        logging.error(f"Error assembling meeting timestamp: {e}")
        return None

def extract_release_date(doc_div):
    try:
        minutes_texts = [x.strip() for x in doc_div.text.split("\n")]
        minutes_date = [x for x in minutes_texts if x.startswith("(Released")][0]
        minutes_date = minutes_date.split("(Released")[-1].replace(")", "").strip()
        return parser.parse(minutes_date)
    except Exception as e:
        logging.error(f"Error extracting release date: {e}")
        return None

def process_document_links(doc_div, doc_type, meeting_timestamp, most_recent_date, new_comms):
    if doc_div and meeting_timestamp > most_recent_date:
        html_link = next(
            (link for link in doc_div.find_all("a") if link.text == "HTML"), None
        )
        if html_link:
            url = BASE_URL + html_link.get("href")
            page_content = fetch_page(url, HEADERS)
            if page_content:
                text = parse_communication_page(page_content, doc_type)
                release_date = meeting_timestamp if doc_type == "Statement" else extract_release_date(doc_div)
                if release_date:
                    new_comms.append({
                        "Date": format_date(meeting_timestamp),
                        "Release Date": format_date(release_date),
                        "Type": doc_type,
                        "Text": text,
                    })

def process_meeting_row(row, meeting_timestamp, most_recent_date, new_comms):
    statement_div = row.find(tag_has_statement)
    if statement_div:
        process_document_links(statement_div, "Statement", meeting_timestamp, most_recent_date, new_comms)

    minutes_div = row.find(tag_has_minutes)
    if minutes_div:
        process_document_links(minutes_div, "Minute", meeting_timestamp, most_recent_date, new_comms)

def scrape_communications(panels, most_recent_date):
    new_comms = []
    for panel in panels:
        year = extract_year_from_panel(panel)
        for row in panel.select('div[class*="row fomc-meeting"]'):
            meeting_timestamp = assemble_meeting_timestamp(row, year)
            if meeting_timestamp:
                process_meeting_row(row, meeting_timestamp, most_recent_date, new_comms)
    return new_comms

def update_communications(new_comms):
    """Updates the communications CSV file with new data."""
    new_comms_df = pd.DataFrame(new_comms)

    if not new_comms_df.empty:
        try:
            communications = pd.read_csv("communications.csv")
        except FileNotFoundError:
            communications = pd.DataFrame(columns=["Date", "Release Date", "Type", "Text"])

        communications = (
            pd.concat([new_comms_df, communications])
            .assign(Date=lambda df: pd.to_datetime(df["Date"]))
            .assign(ReleaseDate=lambda df: pd.to_datetime(df["Release Date"], errors="coerce"))
            .drop(columns=["Release Date"])
            .rename(columns={"ReleaseDate": "Release Date"})
            .sort_values("Date", ascending=False)
            .drop_duplicates()
            .reset_index(drop=True)[["Date", "Release Date", "Type", "Text"]]
        )

        communications.to_csv("communications.csv", index=False)

        # Convert the max release date to datetime before writing
        max_release_date = pd.to_datetime(new_comms_df["Release Date"]).max()
        write_most_recent_date("most-recent-communication-date.txt", max_release_date)


def main():
    most_recent_date = read_most_recent_date("most-recent-communication-date.txt")
    html_content = fetch_page(BASE_URL + MONETARY_POLICY_URL, HEADERS)
    if html_content:
        panels = parse_fomc_page(html_content)
        new_comms = scrape_communications(panels, most_recent_date)
        if new_comms:
            update_communications(new_comms)

if __name__ == "__main__":
    main()