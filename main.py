from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pdf2image import convert_from_bytes
import pytesseract
import pandas as pd
import re, os, openai, datetime
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

# CORS for Famous.ai frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ENV vars
SUPABASE_URL = os.getenv("https://onzlljkjwrttorennlim.supabase.co")
SUPABASE_KEY = os.getenv("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9uemxsamtqd3J0dG9yZW5ubGltIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc0OTczMTc5NCwiZXhwIjoyMDY1MzA3Nzk0fQ.nLH8Hrs6o9yhh506k-mCElXEJRkcGAeaznvtsfJMYsc")
OPENAI_KEY = os.getenv("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")
ASSISTANT_ID = "asst_3xuAoWFIbwtxti7mWUvONOtL"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai.api_key = OPENAI_KEY

# Extraction logic
def extract_title_info(text):
    def find(label, pattern):
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else "[MISSING]"

    return {
        "File Number": find("File Number", r"File Number[:\s]*([\w\-]+)"),
        "Instrument Type": find("Instrument Type", r"Instrument Type[:\s]*([\w\s]+)"),
        "Rank": find("Rank", r"Rank[:\s]*([\w\s]+)"),
        "Amount": find("Amount", r"Amount[:\s]*\\$?([\d,\.]+)"),
        "Document Date": find("Document Date", r"Document Date[:\s]*([\d/\\-]+)"),
        "Recorded Date": find("Recorded Date", r"Recorded Date[:\s]*([\d/\\-]+)"),
        "Book": find("Book", r"Book[:\s]*([\w\-]+)"),
        "Page": find("Page", r"Page[:\s]*([\w\-]+)"),
        "Document Number": find("Document Number", r"Document Number[:\s]*([\w\-]+)"),
        "Note": find("Note", r"Note[:\s]*(.+)"),
        "Lienholder Name": find("Lienholder Name", r"Lienholder Name[:\s]*(.+)"),
        "Optional (Blank)": ""
    }

def extract_legal_desc(text):
    match = re.search(r"Legal Description[:\s]*(.+)", text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else "[MISSING]"

def gpt_fill_missing_fields(ocr_text, extracted):
    missing_keys = [k for k, v in extracted.items() if v == "[MISSING]"]
    if not missing_keys:
        return extracted

    prompt = f"""You are a title report extraction assistant. 
Given the following OCR text, fill in ONLY these missing fields: {missing_keys}.
OCR TEXT:\n\n{ocr_text}\n\nReturn your response as JSON: {{ "Field": "Value" }}"""

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            { "role": "system", "content": "You're an expert at extracting structured fields from scanned real estate title reports." },
            { "role": "user", "content": prompt }
        ],
        temperature=0.1
    )

    data = response.choices[0].message.content
    try:
        import json
        fix = json.loads(data)
        for k in fix:
            if extracted.get(k) == "[MISSING]":
                extracted[k] = fix[k]
    except:
        pass  # fallback: don’t overwrite anything
    return extracted

@app.post("/extract-batch")
async def extract_data(files: list[UploadFile] = File(...)):
    session_id = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    title_data, legal_data = [], []
    total_pages, total_files = 0, len(files)

    for uploaded_file in files:
        pdf_name = uploaded_file.filename
        contents = await uploaded_file.read()
        images = convert_from_bytes(contents)
        total_pages += len(images)
        full_text = ""

        for img in images:
            full_text += pytesseract.image_to_string(img)

        title_row = extract_title_info(full_text)
        title_row = gpt_fill_missing_fields(full_text, title_row)
        title_row["source_file"] = pdf_name

        legal_row = {
            "File Number": title_row["File Number"],
            "Legal Description": extract_legal_desc(full_text),
            "source_file": pdf_name
        }

        title_data.append(title_row)
        legal_data.append(legal_row)

    title_df = pd.DataFrame(title_data)
    legal_df = pd.DataFrame(legal_data)

    # Log session to Supabase
    supabase.table("sessions").insert({
        "session_id": session_id,
        "total_files": total_files,
        "total_pages": total_pages,
        "total_rows": len(title_data),
        "invoice_amount": float(total_pages * 0.10)
    }).execute()

    return {
        "preview1": title_df.to_csv(index=False),
        "preview2": legal_df.to_csv(index=False)
    }
