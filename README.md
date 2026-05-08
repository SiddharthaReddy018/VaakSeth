# VaakSetu — Voice Bridge for Bharat

> AI-powered multilingual grievance redressal platform for Indian citizens.

VaakSetu (“Voice Bridge”) enables citizens to report civic issues in their native language using voice or text. The platform supports multilingual and transliterated inputs (Hinglish, Manglish, Tanglish, etc.) and automatically routes complaints to the appropriate government department using AI-powered intent detection.

---

# Features

* Multilingual voice + text complaint registration
* Supports Kannada, Hindi, Tamil, Telugu, Malayalam, and English
* Romanized language handling (Hinglish, Manglish, etc.)
* AI-powered intent and entity extraction
* Emotion/fear detection for emergency escalation
* Real-time complaint routing
* Live government agent dashboard
* Speech-to-text and text-to-speech pipeline
* Free-tier deployable architecture

---

# Supported Complaint Categories

* Water Supply Issues
* Power Outages
* Potholes & Road Damage
* Garbage & Sanitation
* Emergency Situations
* Police Complaints
* Fire Incidents
* Revenue & Civic Issues

---

# Supported Departments

* BBMP
* BESCOM
* BWSSB
* Police Department
* Fire Department
* Revenue Department
* DMER
* Emergency Services

---

# System Architecture

```text
Speech/Text Input
        ↓
Language Detection
        ↓
Transliteration
        ↓
Translation
        ↓
LLM-based Intent & Entity Extraction
        ↓
Department Routing
        ↓
Voice/Text Response in User Language
```

---

# Tech Stack

## AI / NLP

* Groq Llama-3.1
* Gemini 2.0 Flash (fallback)
* Multilingual intent detection
* Emotion/Fear analysis

## Speech Processing

* Web Speech API
* Whisper ASR
* edge-tts

## Backend

* Flask
* Flask-SocketIO

## Frontend

* Real-time Agent Dashboard
* Live complaint monitoring

---

# Project Structure

```text
VaakSetu/
│
├── backend/
│   ├── app.py
│   ├── routes/
│   ├── services/
│   └── models/
│
├── frontend/
│   ├── src/
│   └── public/
│
├── dashboard/
│
├── static/
├── templates/
├── requirements.txt
└── README.md
```

---

# Installation

## Clone Repository

```bash
git clone https://github.com/your-username/vaaksetu.git
cd vaaksetu
```

---

# Create Virtual Environment

## Windows

```bash
python -m venv venv
venv\Scripts\activate
```

## Linux / macOS

```bash
python3 -m venv venv
source venv/bin/activate
```

---

# Install Dependencies

```bash
pip install -r requirements.txt
```

---

# Environment Variables

Create a `.env` file:

```env
GROQ_API_KEY=your_groq_key
GEMINI_API_KEY=your_gemini_key
OPENROUTER_API_KEY=your_openrouter_key
```

---

# Run Backend

```bash
python app.py
```

---

# Run Frontend

```bash
npm install
npm run dev
```

---

# Example Workflow

1. Citizen speaks in Kannada/Hindi/Tamil/etc.
2. Speech converted to text
3. Language automatically detected
4. Text transliterated and translated
5. LLM extracts:

   * Complaint category
   * Severity
   * Location
   * Department
6. Complaint routed to authority
7. Citizen receives multilingual voice response

---

# Example Complaints

```text
"3 days ninda water bartha illa"
→ BWSSB → Water Supply Complaint
```

```text
"Current illa since morning"
→ BESCOM → Power Outage
```

```text
"Road mele dodda pothole ide"
→ BBMP → Road Maintenance
```

---

# Future Improvements

* WhatsApp integration
* IVR phone-call support
* Geo-location based routing
* Complaint tracking IDs
* SMS notifications
* Analytics dashboard
* Offline-first deployment

---

# Vision

VaakSetu aims to bridge the language barrier between citizens and government services by enabling accessible, inclusive, and intelligent civic grievance reporting for Bharat.

---


# Contributors

* Siddhartha Reddy
* Sai Harsha

---

# Acknowledgements

* Groq
* Google Gemini
* Whisper
* Flask
* Open-source AI community
Minor change in Readme
