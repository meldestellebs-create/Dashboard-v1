"""
SPoT – Smart Protocol | FastAPI Backend
========================================
Läuft lokal auf deinem PC (kein GPU nötig).
Startet mit: python main.py

Voraussetzungen (einmalig installieren):
  pip install fastapi uvicorn python-multipart pymupdf anthropic python-dotenv

API-Key: Lege eine .env Datei an mit:
  ANTHROPIC_API_KEY=sk-ant-...
"""

import os, json, uuid, re
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# ── Optionale Abhängigkeiten (graceful fallback) ──
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("⚠  PyMuPDF nicht installiert – PDF-Textextraktion deaktiviert")

try:
    import anthropic
    CLAUDE_SUPPORT = True
except ImportError:
    CLAUDE_SUPPORT = False
    print("⚠  anthropic nicht installiert – KI-Funktionen deaktiviert")

load_dotenv()

# ── Pfade ──
BASE_DIR     = Path(__file__).parent
DATA_DIR     = BASE_DIR / "data"
KB_DIR       = DATA_DIR / "knowledge_base"   # PDF-Wissensbasis
CLIENT_DIR   = DATA_DIR / "client_docs"      # Klienten-Dokumente
EXPORTS_DIR  = DATA_DIR / "exports"          # Generierte Dokumente
DB_FILE      = DATA_DIR / "klienten.json"    # Klienten-Datenbank

for d in [DATA_DIR, KB_DIR, CLIENT_DIR, EXPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Klienten-DB initialisieren ──
def load_db() -> dict:
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    default = {"klienten": [], "kb_docs": [], "sessions": []}
    save_db(default)
    return default

def save_db(data: dict):
    DB_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ── FastAPI App ──
app = FastAPI(
    title="SPoT Smart Protocol API",
    description="Lokales KI-Backend für das Übergangsmanagement",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Im lokalen Betrieb OK
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dashboard HTML direkt ausliefern
@app.get("/", response_class=FileResponse)
async def serve_dashboard():
    html = BASE_DIR / "SPoT_Dashboard_v3.html"
    if html.exists():
        return FileResponse(html)
    return JSONResponse({"status": "SPoT API läuft", "dashboard": "SPoT_Dashboard_v3.html nicht gefunden"})

# ══════════════════════════════════════════════════════
#  1. KLIENTEN-VERWALTUNG
# ══════════════════════════════════════════════════════

class KlientCreate(BaseModel):
    name: str
    alter: int
    schule: Optional[str] = ""
    klasse: Optional[str] = ""
    zielgruppe: Optional[str] = "Sek. I (Realschule)"
    interessen: Optional[list] = []
    notizen: Optional[str] = ""

class SessionCreate(BaseModel):
    klient_id: str
    leitfragen: Optional[dict] = {}
    checkliste: Optional[dict] = {}
    interessen: Optional[list] = []

@app.get("/api/klienten")
async def get_klienten():
    db = load_db()
    return {"klienten": db["klienten"], "count": len(db["klienten"])}

@app.get("/api/klienten/{klient_id}")
async def get_klient(klient_id: str):
    db = load_db()
    k = next((k for k in db["klienten"] if k["id"] == klient_id), None)
    if not k:
        raise HTTPException(404, "Klient nicht gefunden")
    return k

@app.post("/api/klienten")
async def create_klient(klient: KlientCreate):
    db = load_db()
    new_id = f"K-{str(uuid.uuid4())[:8].upper()}"
    entry = {
        "id": new_id,
        "name": klient.name,
        "alter": klient.alter,
        "schule": klient.schule,
        "klasse": klient.klasse,
        "zielgruppe": klient.zielgruppe,
        "interessen": klient.interessen,
        "notizen": klient.notizen,
        "erstellt": datetime.now().isoformat(),
        "status": "aktiv",
        "sitzungen": [],
        "dokumente": []
    }
    db["klienten"].append(entry)
    save_db(db)
    return {"success": True, "klient": entry}

@app.put("/api/klienten/{klient_id}")
async def update_klient(klient_id: str, klient: KlientCreate):
    db = load_db()
    for i, k in enumerate(db["klienten"]):
        if k["id"] == klient_id:
            db["klienten"][i].update({
                "name": klient.name,
                "alter": klient.alter,
                "schule": klient.schule,
                "klasse": klient.klasse,
                "zielgruppe": klient.zielgruppe,
                "interessen": klient.interessen,
                "notizen": klient.notizen,
                "aktualisiert": datetime.now().isoformat()
            })
            save_db(db)
            return {"success": True, "klient": db["klienten"][i]}
    raise HTTPException(404, "Klient nicht gefunden")

@app.delete("/api/klienten/{klient_id}")
async def delete_klient(klient_id: str):
    db = load_db()
    db["klienten"] = [k for k in db["klienten"] if k["id"] != klient_id]
    save_db(db)
    return {"success": True}

# ══════════════════════════════════════════════════════
#  2. SITZUNGEN
# ══════════════════════════════════════════════════════

@app.post("/api/sitzungen")
async def create_session(session: SessionCreate):
    db = load_db()
    klient = next((k for k in db["klienten"] if k["id"] == session.klient_id), None)
    if not klient:
        raise HTTPException(404, "Klient nicht gefunden")
    sess_id = f"S-{str(uuid.uuid4())[:8].upper()}"
    entry = {
        "id": sess_id,
        "klient_id": session.klient_id,
        "klient_name": klient["name"],
        "datum": datetime.now().isoformat(),
        "leitfragen": session.leitfragen,
        "checkliste": session.checkliste,
        "interessen": session.interessen,
        "status": "aktiv",
        "transkript": "",
        "ki_analyse": None
    }
    db["sessions"].append(entry)
    # Sitzungs-Referenz beim Klienten speichern
    for k in db["klienten"]:
        if k["id"] == session.klient_id:
            k["sitzungen"].append({"id": sess_id, "datum": entry["datum"]})
    save_db(db)
    return {"success": True, "session": entry}

@app.get("/api/sitzungen/{session_id}")
async def get_session(session_id: str):
    db = load_db()
    s = next((s for s in db["sessions"] if s["id"] == session_id), None)
    if not s:
        raise HTTPException(404, "Sitzung nicht gefunden")
    return s

# ══════════════════════════════════════════════════════
#  3. DOKUMENT-UPLOAD & PDF-EXTRAKTION
# ══════════════════════════════════════════════════════

def extract_pdf_text(file_path: Path) -> str:
    """Extrahiert Text aus PDF – nur CPU, kein GPU nötig."""
    if not PDF_SUPPORT:
        return "[PyMuPDF nicht installiert – Text nicht extrahiert]"
    try:
        doc = fitz.open(str(file_path))
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text[:50000]  # Max 50k Zeichen
    except Exception as e:
        return f"[Fehler bei PDF-Extraktion: {e}]"

def detect_doc_category(filename: str) -> str:
    n = filename.lower()
    if any(x in n for x in ["lebenslauf", "cv", "vita"]): return "Lebenslauf"
    if any(x in n for x in ["zeugnis", "note", "abschluss"]): return "Zeugnis"
    if any(x in n for x in ["zertifikat", "kurs", "bescheinigung"]): return "Zertifikat"
    if any(x in n for x in ["motivat", "anschreiben"]): return "Motivationsschreiben"
    if any(x in n for x in ["regional", "schrift"]): return "Regionalschrift"
    if any(x in n for x in ["schul", "verzeichnis"]): return "Schulverzeichnis"
    if any(x in n for x in ["frist", "termin", "datum"]): return "Fristen"
    if any(x in n for x in ["ausbildung", "beruf"]): return "Ausbildung"
    return "Allgemein"

@app.post("/api/upload/knowledge-base")
async def upload_kb_doc(file: UploadFile = File(...)):
    """PDF in die Knowledge Base hochladen."""
    if len(list(KB_DIR.iterdir())) >= 500:
        raise HTTPException(400, "Maximum 500 Dokumente erreicht")
    safe_name = re.sub(r'[^\w\.\-]', '_', file.filename)
    dest = KB_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)

    # Text extrahieren für späteres RAG
    text = ""
    if file.filename.lower().endswith(".pdf"):
        text = extract_pdf_text(dest)
    elif file.filename.lower().endswith(".txt"):
        text = content.decode("utf-8", errors="ignore")[:50000]

    # In DB speichern
    db = load_db()
    if "kb_docs" not in db:
        db["kb_docs"] = []
    doc_entry = {
        "id": str(uuid.uuid4()),
        "name": safe_name,
        "original_name": file.filename,
        "kategorie": detect_doc_category(file.filename),
        "groesse": f"{len(content)/1048576:.1f} MB",
        "status": "indexed",
        "datum": datetime.now().strftime("%d.%m."),
        "text_preview": text[:500] if text else "",
        "hat_text": bool(text)
    }
    db["kb_docs"].append(doc_entry)
    save_db(db)
    return {"success": True, "doc": doc_entry, "text_extracted": bool(text)}

@app.post("/api/upload/client-doc/{klient_id}")
async def upload_client_doc(klient_id: str, file: UploadFile = File(...)):
    """Klienten-Dokument hochladen."""
    db = load_db()
    klient = next((k for k in db["klienten"] if k["id"] == klient_id), None)
    if not klient:
        raise HTTPException(404, "Klient nicht gefunden")
    safe_name = re.sub(r'[^\w\.\-]', '_', file.filename)
    client_folder = CLIENT_DIR / klient_id
    client_folder.mkdir(exist_ok=True)
    dest = client_folder / safe_name
    content = await file.read()
    dest.write_bytes(content)

    text = ""
    if file.filename.lower().endswith(".pdf"):
        text = extract_pdf_text(dest)

    doc_entry = {
        "id": str(uuid.uuid4()),
        "name": safe_name,
        "original_name": file.filename,
        "kategorie": detect_doc_category(file.filename),
        "status": "indexed" if text else "processing",
        "datum": datetime.now().strftime("%d.%m."),
        "hat_text": bool(text)
    }
    klient["dokumente"].append(doc_entry)
    save_db(db)
    return {"success": True, "doc": doc_entry}

@app.get("/api/knowledge-base")
async def get_kb_docs():
    db = load_db()
    return {"docs": db.get("kb_docs", []), "count": len(db.get("kb_docs", []))}

@app.delete("/api/knowledge-base/{doc_id}")
async def delete_kb_doc(doc_id: str):
    db = load_db()
    doc = next((d for d in db.get("kb_docs", []) if d["id"] == doc_id), None)
    if doc:
        f = KB_DIR / doc["name"]
        if f.exists(): f.unlink()
        db["kb_docs"] = [d for d in db["kb_docs"] if d["id"] != doc_id]
        save_db(db)
    return {"success": True}

# ══════════════════════════════════════════════════════
#  4. KI-ANALYSE (Claude API)
# ══════════════════════════════════════════════════════

def get_claude_client():
    if not CLAUDE_SUPPORT:
        return None
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)

def build_rag_context(klient_id: str, query: str, max_docs: int = 3) -> str:
    """Einfaches Keyword-RAG aus KB-Dokumenten (kein Vektorstore nötig)."""
    db = load_db()
    kb_docs = db.get("kb_docs", [])
    relevant = []
    query_lower = query.lower()
    keywords = query_lower.split()

    for doc in kb_docs:
        if not doc.get("hat_text"):
            continue
        # Einfaches Keyword-Matching auf Text-Preview
        preview = doc.get("text_preview", "").lower()
        score = sum(1 for kw in keywords if kw in preview)
        if score > 0:
            relevant.append((score, doc))

    relevant.sort(key=lambda x: x[0], reverse=True)
    context_parts = []
    for _, doc in relevant[:max_docs]:
        context_parts.append(
            f"[Dokument: {doc['name']} | Kategorie: {doc['kategorie']}]\n"
            f"{doc.get('text_preview', '')}\n"
        )
    return "\n---\n".join(context_parts) if context_parts else ""

class AnalyseRequest(BaseModel):
    klient_id: str
    session_id: Optional[str] = None
    leitfragen: Optional[dict] = {}
    interessen: Optional[list] = []
    transkript: Optional[str] = ""
    modus: Optional[str] = "bewerbermappe"  # bewerbermappe | coaching | analyse

@app.post("/api/ki/analysiere")
async def ki_analyse(req: AnalyseRequest):
    """
    Quad-Fusion: SPoT-Protokoll + Klienten-Daten + KB-RAG + KI
    Läuft über Claude API (kein lokales Modell nötig).
    """
    client = get_claude_client()
    if not client:
        return {
            "success": False,
            "error": "ANTHROPIC_API_KEY nicht gesetzt. Lege .env Datei an.",
            "demo": _demo_analyse(req)
        }

    db = load_db()
    klient = next((k for k in db["klienten"] if k["id"] == req.klient_id), None)
    if not klient:
        raise HTTPException(404, "Klient nicht gefunden")

    # RAG-Kontext aus Knowledge Base
    search_query = " ".join(req.interessen) + " " + req.transkript[:200]
    rag_context = build_rag_context(req.klient_id, search_query)

    # Klienten-Dokumente als Kontext
    client_docs_info = "\n".join([
        f"- {d['name']} ({d['kategorie']})"
        for d in klient.get("dokumente", [])
    ])

    # Prompt je nach Modus
    prompts = {
        "bewerbermappe": f"""Du bist ein erfahrener Berufsberater im Übergangsmanagement.
Erstelle eine strukturierte Bewerbermappe-Vorlage für diesen Klienten.

KLIENT:
Name: {klient['name']}, {klient['alter']} Jahre
Schule: {klient.get('schule','')} {klient.get('klasse','')}
Zielgruppe: {klient.get('zielgruppe','')}
Interessen: {', '.join(req.interessen or klient.get('interessen',[]))}

SITZUNGSNOTIZEN:
{json.dumps(req.leitfragen, ensure_ascii=False, indent=2)}

VORHANDENE DOKUMENTE DES KLIENTEN:
{client_docs_info or 'Keine Dokumente vorhanden'}

FACHWISSEN AUS KNOWLEDGE BASE:
{rag_context or 'Keine relevanten Dokumente gefunden'}

Erstelle:
1. Kurzprofil des Klienten (3-4 Sätze)
2. Empfohlene Ausbildungsberufe (3 Vorschläge mit Begründung)
3. Nächste Schritte (konkrete ToDos)
4. Offene Punkte (was fehlt noch)

Antworte auf Deutsch, strukturiert und konkret.""",

        "coaching": f"""Du bist ein Methodik-Coach nach Galuske.
Analysiere diese Beratungssitzung und gib Feedback.

TRANSKRIPT/NOTIZEN:
{req.transkript or json.dumps(req.leitfragen, ensure_ascii=False)}

Analysiere:
1. Redeanteil Berater vs. Klient (schätze %)
2. Offene vs. geschlossene Fragen
3. Verbesserungsvorschläge (konkret, 3 Punkte)
4. Positiv-Feedback (was lief gut)

Antworte sachlich und konstruktiv auf Deutsch.""",

        "analyse": f"""Analysiere das folgende SPoT-Protokoll auf Vollständigkeit und Widersprüche.

PROTOKOLL:
{json.dumps(req.leitfragen, ensure_ascii=False, indent=2)}

TRANSKRIPT:
{req.transkript[:2000] if req.transkript else 'Kein Transkript'}

Prüfe:
1. Vollständigkeit (fehlen wichtige Infos?)
2. Widersprüche im Protokoll
3. KI-Vorschläge zur Ergänzung (max 3)
4. Konsistenz-Score (0-100%)

Format: JSON mit Feldern: vollstaendigkeit, widersprueche, vorschlaege, konsistenz_score"""
    }

    prompt = prompts.get(req.modus, prompts["bewerbermappe"])

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        result_text = message.content[0].text

        # Bei Analyse-Modus JSON parsen
        result_data = {"text": result_text}
        if req.modus == "analyse":
            try:
                clean = result_text.replace("```json","").replace("```","").strip()
                result_data = json.loads(clean)
            except:
                result_data = {"text": result_text}

        # In Session speichern
        if req.session_id:
            for s in db["sessions"]:
                if s["id"] == req.session_id:
                    s["ki_analyse"] = {
                        "modus": req.modus,
                        "ergebnis": result_data,
                        "datum": datetime.now().isoformat()
                    }
            save_db(db)

        return {"success": True, "modus": req.modus, "ergebnis": result_data}

    except Exception as e:
        return {"success": False, "error": str(e), "demo": _demo_analyse(req)}

def _demo_analyse(req: AnalyseRequest) -> dict:
    """Fallback wenn kein API-Key vorhanden."""
    return {
        "text": f"""[DEMO – kein API-Key] 
Klient-ID: {req.klient_id}
Modus: {req.modus}
Interessen: {', '.join(req.interessen or [])}

Echte KI-Analyse verfügbar sobald ANTHROPIC_API_KEY in .env gesetzt ist.
Empfehlung: Ausbildung im Bereich Technik/Handwerk prüfen."""
    }

# ══════════════════════════════════════════════════════
#  5. STATISTIKEN FÜR REPORTS
# ══════════════════════════════════════════════════════

@app.get("/api/stats")
async def get_stats():
    db = load_db()
    klienten = db["klienten"]
    sessions = db["sessions"]
    kb_docs  = db.get("kb_docs", [])

    by_zielgruppe = {}
    for k in klienten:
        zg = k.get("zielgruppe", "Unbekannt")
        by_zielgruppe[zg] = by_zielgruppe.get(zg, 0) + 1

    return {
        "gesamt_klienten": len(klienten),
        "aktive_sitzungen": len([s for s in sessions if s.get("status") == "aktiv"]),
        "kb_dokumente": len(kb_docs),
        "kb_indiziert": len([d for d in kb_docs if d.get("status") == "indexed"]),
        "by_zielgruppe": by_zielgruppe,
        "letzte_sitzungen": sessions[-5:][::-1] if sessions else []
    }

# ══════════════════════════════════════════════════════
#  6. HEALTH CHECK
# ══════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    return {
        "status": "ok",
        "pdf_support": PDF_SUPPORT,
        "claude_api": CLAUDE_SUPPORT and has_key,
        "claude_sdk": CLAUDE_SUPPORT,
        "api_key_set": has_key,
        "kb_docs": len(load_db().get("kb_docs", [])),
        "klienten": len(load_db()["klienten"]),
        "version": "1.0.0"
    }

# ── Start ──
if __name__ == "__main__":
    print("\n" + "="*50)
    print("  SPoT Smart Protocol – Backend startet...")
    print("="*50)
    print(f"  Dashboard:  http://localhost:8000")
    print(f"  API-Docs:   http://localhost:8000/docs")
    print(f"  Health:     http://localhost:8000/api/health")
    print(f"  PDF-Support: {'✓' if PDF_SUPPORT else '✗ pip install pymupdf'}")
    print(f"  Claude API:  {'✓' if CLAUDE_SUPPORT else '✗ pip install anthropic'}")
    print(f"  API-Key:     {'✓ gesetzt' if os.getenv('ANTHROPIC_API_KEY') else '✗ .env Datei anlegen!'}")
    print("="*50 + "\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
