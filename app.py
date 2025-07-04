import re
from flask import Flask, jsonify, render_template, request
import json
from flask_cors import CORS
from oauth2client.service_account import ServiceAccountCredentials
import gspread
app = Flask(__name__)

# Accesso a Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credential.json", scope)
client = gspread.authorize(creds)
CORS(app)

def estrai_numero_canali(valore):
    """Estrae il numero di canali da una stringa tipo '1CH', 'RGBW - 4CH', ecc."""
    if not valore:
        return None
    match = re.search(r'(\d+)\s*CH', str(valore).upper())
    if match:
        return int(match.group(1))
    return None

# NUOVE FUNZIONI AGGIUNTE - Inserire qui
def estrai_temperatura_colore(item):
    """Estrae la temperatura colore da una strip LED"""
    if not item:
        return None
    
    # Cerca nel campo "Colore Luce" o "Descrizione"
    testo = str(item.get('Colore Luce', '') + ' ' + item.get('Descrizione', '') + ' ' + item.get('Codice', '')).upper()
    
    # Pattern per trovare temperature in Kelvin
    kelvin_patterns = [
        r'(\d{4})K',  # 3000K, 4000K, etc.
        r'(\d{4})\s*KELVIN',
        r'(\d{4})\s*°K'
    ]
    
    temperature_trovate = []
    for pattern in kelvin_patterns:
        matches = re.findall(pattern, testo)
        for match in matches:
            temp = int(match)
            if 1000 <= temp <= 10000:  # Range ragionevole per temperature colore
                temperature_trovate.append(temp)
    
    if temperature_trovate:
        # Se ci sono più temperature, prendi la prima (o potresti fare una media)
        return min(temperature_trovate)
    
    # Fallback: cerca parole chiave comuni
    if any(keyword in testo for keyword in [ '2700', '2800', '2900']):
        return 2700  # Bianco caldo tipico
    elif any(keyword in testo for keyword in [ '4000']):
        return 4000  # Bianco naturale
    elif any(keyword in testo for keyword in [ '6000', '6500']):
        return 6000  # Bianco freddo
    
    return None

def determina_categoria_canali_strip(item):
    """Determina se una strip appartiene alla categoria 1-2CH o 3-5CH based sulla temperatura colore"""
    if not item:
        return None
    
    temp_colore = estrai_temperatura_colore(item)
    
    if temp_colore is None:
        # Se non riusciamo a determinare la temperatura, usiamo il vecchio sistema basato sui canali
        num_canali = estrai_numero_canali(item.get("Canali", ""))
        if num_canali:
            return "1-2CH" if num_canali <= 2 else "3-5CH"
        return None
    
    # Logica principale: < 3000K = 1-2CH, >= 3000K = 3-5CH
    return "1-2CH" if temp_colore <= 3000 else "3-5CH"

def determina_categoria_canali_dimmer(item):
    """Determina la categoria di canali del dimmer"""
    if not item:
        return None
    
    num_canali = estrai_numero_canali(item.get("Canali Dimmer", ""))
    if num_canali is None:
        return None
    
    return "1-2CH" if num_canali <= 2 else "3-5CH"

# NUOVE FUNZIONI PER ALIMENTATORI
def estrai_potenza_strip(potenza_str):
    """Estrae la potenza per metro da una stringa tipo '4,8W/m'"""
    if not potenza_str:
        return None
    
    # Pattern per trovare potenza per metro
    match = re.search(r'(\d+(?:[.,]\d+)?)\s*W/m', str(potenza_str).upper())
    if match:
        return float(match.group(1).replace(',', '.'))
    
    return None

def estrai_voltaggio_strip(voltaggio_str):
    """Estrae il voltaggio da una stringa tipo '24VDC'"""
    if not voltaggio_str:
        return None
    
    # Pulisce e estrae il numero
    cleaned = str(voltaggio_str).upper().replace('VDC', '').replace('VAC', '').replace('V', '').strip()
    match = re.search(r'(\d+(?:[.,]\d+)?)', cleaned)
    if match:
        return float(match.group(1).replace(',', '.'))
    
    return None

def calcola_ampere_necessari(strip, metri):
    """Calcola gli ampere necessari per una strip in base ai metri"""
    if not strip or not metri or metri <= 0:
        return None
    
    potenza_per_metro = estrai_potenza_strip(strip.get('Potenza', ''))
    voltaggio = estrai_voltaggio_strip(strip.get('Input Volt', ''))
    
    if potenza_per_metro is None or voltaggio is None:
        return None
    
    # Calcolo: Ampere = (Potenza_totale) / Voltaggio
    potenza_totale = potenza_per_metro * metri
    ampere_necessari = potenza_totale / voltaggio
    
    return round(ampere_necessari, 3)

def trova_alimentatori_compatibili(ampere_necessari, alimentatori_data, margine_sicurezza=1.2):
    """Trova alimentatori compatibili con margine di sicurezza"""
    if ampere_necessari is None:
        return []
    
    alimentatori_compatibili = []
    ampere_minimi = ampere_necessari * margine_sicurezza
    
    for alimentatore in alimentatori_data:
        corrente_alimentatore = alimentatore.get('corrente_A')
        if corrente_alimentatore is not None and corrente_alimentatore >= ampere_minimi:
            # Aggiungi informazioni di compatibilità
            alimentatore_info = alimentatore.copy()
            alimentatore_info['margine_utilizzazione'] = round((ampere_necessari / corrente_alimentatore) * 100, 1)
            alimentatori_compatibili.append(alimentatore_info)
    
    # Ordina per corrente crescente (più efficiente)
    alimentatori_compatibili.sort(key=lambda x: x.get('corrente_A', 0))
    
    return alimentatori_compatibili

# FINE NUOVE FUNZIONI

def profilo_colore_strip(item):
    """Determina il profilo colore di una strip o dimmer basandosi sui canali o descrizione"""
    if not item:
        return None
    
    # Controlla prima i canali
    canali_raw = item.get("Canali", "") or item.get("Canali Dimmer", "")
    num_canali = estrai_numero_canali(canali_raw)
    
    if num_canali:
        if num_canali == 1:
            return "MONO"  # Monocromatico
        elif num_canali == 2:
            return "CCT"   # Color Temperature (bianco variabile)
        elif num_canali == 3:
            return "RGB"   # RGB
        elif num_canali == 4:
            return "RGBW"  # RGB + White
        elif num_canali >= 5:
            return "MULTI" # Multi-canale
    
    # Fallback: analisi del nome/descrizione
    descrizione = str(item.get('Descrizione', '') + ' ' + item.get('Codice', '')).upper()
    
    if 'RGBW' in descrizione:
        return "RGBW"
    elif 'RGB' in descrizione:
        return "RGB"
    elif any(temp in descrizione for temp in ['3000K', '4000K', '6000K', 'CCT', 'TUNABLE']):
        return "CCT"
    else:
        return "MONO"
def get_sheet_data(sheet_name):
    """Legge i dati da un foglio specifico di Google Sheets"""
    try:
        sheet = client.open("NOME_TUO_FOGLIO_GOOGLE_SHEETS").worksheet(sheet_name)
        records = sheet.get_all_records()
        return records
    except Exception as e:
        print(f"Errore nel leggere il foglio {sheet_name}: {str(e)}")
        return []

def load_all_data():
    """Carica tutti i dati dai fogli Google Sheets"""
    return {
        "stripled": get_sheet_data("Strips"),
        "profili": get_sheet_data("Profiles"),
        "Dimmer": get_sheet_data("Dimmer"),
        "alimentatori": get_sheet_data("PowerSupplies")
    }

# Carica i dati all'avvio
all_data = load_all_data()

# Dati caricati direttamente da Google Sheets
strip_data = all_data["stripled"]
profili_data = all_data["profili"]
dimmer_data = all_data["Dimmer"]
alimentatori_data = all_data["alimentatori"]



# Funzioni di utilità migliorate
def pulisci_voltaggio(valore):
    """Pulisce una stringa voltaggio rimuovendo prefissi e suffissi comuni"""
    if not valore:
        return ""
    
    cleaned = str(valore).upper()
    # Rimuovi prefissi/suffissi comuni
    for term in ['DC', 'AC', 'V']:
        cleaned = cleaned.replace(term, '')
    
    return cleaned.strip()

def estrai_voltaggio_singolo(valore):
    """Estrae un singolo valore di voltaggio"""
    if not valore:
        return None
    
    cleaned = pulisci_voltaggio(valore)
    match = re.search(r'(\d+(?:[.,]\d+)?)', cleaned)
    if match:
        return float(match.group(1).replace(',', '.'))
    return None

def estrai_range_voltaggio_dimmer(valore):
    """Versione migliorata per estrarre range voltaggio dimmer"""
    if not valore:
        return None, None
    
    cleaned = pulisci_voltaggio(valore)
    print(f"Debug voltaggio dimmer: '{valore}' → '{cleaned}'")  # Debug

    range_patterns = [
        r'(\d+(?:[.,]\d+)?)\s*[~\-–]\s*(\d+(?:[.,]\d+)?)',  
        r'(\d+(?:[.,]\d+)?)\s*TO\s*(\d+(?:[.,]\d+)?)',      
        r'(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)'            
    ]
    
    for pattern in range_patterns:
        match = re.search(pattern, cleaned)
        if match:
            min_v = float(match.group(1).replace(',', '.'))
            max_v = float(match.group(2).replace(',', '.'))
            print(f"  → Range trovato: [{min_v}, {max_v}]")  # Debug
            return min_v, max_v
    
    # Se non è un range, prova singolo valore
    single_v = estrai_voltaggio_singolo(valore)
    if single_v is not None:
        print(f"  → Valore singolo: {single_v}")  # Debug
        return single_v, single_v
    
    print("  → Nessun valore trovato")  # Debug
    return None, None

def estrai_larghezza_strip(dimensioni):
    if not dimensioni:
        return None
    match = re.search(r'\d+[xX×](\d+(?:[.,]\d+)?)[xX×]\d+', str(dimensioni))
    if match:
        return float(match.group(1).replace(',', '.'))
    return None

def estrai_larghezza_profilo(valore):
    if not valore:
        return None
    match = re.search(r'(\d+(?:[.,]\d+)?)', str(valore))
    if match:
        return float(match.group(1).replace(',', '.'))
    return None

def prepara_dettagli_profilo(profilo):
    """Prepara tutti i dettagli del profilo per la visualizzazione"""
    dettagli = {}
    
    # Lista dei campi che vogliamo mostrare (escludendo 'Codice' che è già mostrato)
    campi_da_mostrare = [
    'Codice', 'Dimensioni', 'Dissipazione Max', 'Larghezza Max Strip',
    'Materiale/Finitura', 'Cover', 'Tappi', 'Ganci'
    ]
    for campo in campi_da_mostrare:
        valore = profilo.get(campo, '')
        if valore and str(valore).strip() and str(valore).strip().lower() not in ['', 'n/a', 'na', '-']:
            dettagli[campo] = str(valore).strip()
    
    # Aggiungi tutti gli altri campi che potrebbero esistere nel foglio
    for chiave, valore in profilo.items():
        if (chiave not in campi_da_mostrare and 
            chiave != 'Codice' and 
            valore and 
            str(valore).strip() and 
            str(valore).strip().lower() not in ['', 'n/a', 'na', '-']):
            dettagli[chiave] = str(valore).strip()
    
    return dettagli

# Dizionari di supporto
strip_larghezze = {
    s['Codice'].strip().upper(): estrai_larghezza_strip(s.get('Dimensioni', ''))
    for s in strip_data if s.get('Codice')
}

profilo_larghezze = {
    p['Codice'].strip().upper(): estrai_larghezza_profilo(p.get('Larghezza Max Strip', ''))
    for p in profili_data if p.get('Codice')
}

dimmer_voltaggi = {
    d['Codice'].strip().upper(): estrai_range_voltaggio_dimmer(d.get('Voltaggio Input', ''))
    for d in dimmer_data if d.get('Codice')
}

@app.route("/")
def index():
    return render_template("index.html")

# NUOVA ROTTA PER CALCOLO ALIMENTATORI
@app.route("/calcola_alimentatori")
def calcola_alimentatori():
    """Calcola alimentatori necessari per una strip e una quantità di metri"""
    codice = request.args.get("codice", "").strip().upper()
    metri = request.args.get("metri", "")
    
    if not codice or not metri:
        return jsonify({"error": "Codice e metri sono obbligatori"}), 400
    
    try:
        metri_float = float(metri)
        if metri_float <= 0:
            return jsonify({"error": "I metri devono essere maggiori di 0"}), 400
    except ValueError:
        return jsonify({"error": "Metri deve essere un numero valido"}), 400
    
    # Trova la strip
    strip = next((s for s in strip_data if s['Codice'].strip().upper() == codice), None)
    if not strip:
        return jsonify({"error": "Strip non trovata"}), 404
    
    # Calcola ampere necessari
    ampere_necessari = calcola_ampere_necessari(strip, metri_float)
    if ampere_necessari is None:
        return jsonify({
            "error": "Impossibile calcolare ampere: dati di potenza o voltaggio mancanti",
            "strip": strip
        }), 400
    
    # Trova alimentatori compatibili
    alimentatori_compatibili = trova_alimentatori_compatibili(ampere_necessari, alimentatori_data)
    
    # Informazioni di debug
    potenza_per_metro = estrai_potenza_strip(strip.get('Potenza', ''))
    voltaggio = estrai_voltaggio_strip(strip.get('Input Volt', ''))
    potenza_totale = potenza_per_metro * metri_float if potenza_per_metro else None
    
    return jsonify({
        "strip": strip,
        "metri": metri_float,
        "calcoli": {
            "potenza_per_metro": potenza_per_metro,
            "voltaggio": voltaggio,
            "potenza_totale": potenza_totale,
            "ampere_necessari": ampere_necessari,
            "margine_sicurezza": 1.2
        },
        "alimentatori_compatibili": alimentatori_compatibili,
        "debug": {
            "num_alimentatori_totali": len(alimentatori_data),
            "num_alimentatori_compatibili": len(alimentatori_compatibili)
        }
    })

# SOSTITUIRE COMPLETAMENTE QUESTA FUNZIONE
@app.route("/cerca")
def cerca():
    codice = request.args.get("codice", "").strip().upper()
    if not codice:
        return jsonify({"error": "Nessun codice fornito"}), 400

    print(f"🔍 Cercando codice: {codice}")

    # --- RICERCA STRIP LED ---
    strip = next((s for s in strip_data if s['Codice'].strip().upper() == codice), None)
    if strip:
        print(f"✅ Strip trovata: {strip['Codice']}")

        larghezza_strip = strip_larghezze.get(codice)
        if larghezza_strip is None:
            return jsonify({"error": "Larghezza strip non trovata"}), 404

        # Profili compatibili (rimane uguale)
        profili_compatibili = []
        for p in profili_data:
            larghezza_profilo = profilo_larghezze.get(p['Codice'].strip().upper())
            if larghezza_profilo is not None and larghezza_profilo >= larghezza_strip:
                profilo_con_dettagli = p.copy()
                profili_compatibili.append(profilo_con_dettagli)

        # LOGICA DIMMER: basata su temperatura colore
        input_volt_strip_float = estrai_voltaggio_singolo(strip.get('Input Volt', ''))
        categoria_canali_strip = determina_categoria_canali_strip(strip)
        temp_colore_strip = estrai_temperatura_colore(strip)

        print(f"🌡️ Strip - Temperatura colore: {temp_colore_strip}K, Categoria: {categoria_canali_strip}")

        dimmer_compatibili = []

        for d in dimmer_data:
            if not d.get('Codice'):
                continue
                
            codice_dimmer = d['Codice'].strip().upper()
            min_v, max_v = estrai_range_voltaggio_dimmer(d.get("Voltaggio Input", ""))
            categoria_canali_dimmer = determina_categoria_canali_dimmer(d)

            # Controlli di compatibilità
            is_volt_compatibile = (
                input_volt_strip_float is not None and 
                min_v is not None and max_v is not None and 
                min_v <= input_volt_strip_float <= max_v
            )
            
            # Compatibilità basata su categoria canali derivata da temperatura colore
            is_canali_compatibile = (
                categoria_canali_strip is not None and 
                categoria_canali_dimmer is not None and 
                categoria_canali_strip == categoria_canali_dimmer
            )

            if is_volt_compatibile and is_canali_compatibile:
                dimmer_compatibili.append(d)
                print(f"✅ {codice_dimmer} compatibile: V={min_v}-{max_v}, Categoria={categoria_canali_dimmer}")
            else:
                motivi = []
                if not is_volt_compatibile:
                    motivi.append(f"voltaggio ({input_volt_strip_float}V non in {min_v}-{max_v}V)")
                if not is_canali_compatibile:
                    motivi.append(f"categoria canali ({categoria_canali_strip} vs {categoria_canali_dimmer})")
                print(f"❌ {codice_dimmer} NON compatibile: {', '.join(motivi)}")

        # NUOVO: Informazioni per calcolo alimentatori
        potenza_per_metro = estrai_potenza_strip(strip.get('Potenza', ''))
        voltaggio_strip = estrai_voltaggio_strip(strip.get('Input Volt', ''))
        
        calcolo_alimentatori_possibile = (potenza_per_metro is not None and voltaggio_strip is not None)

        return jsonify({
            "tipo": "stripled",
            "strip": strip,
            "profili_compatibili": profili_compatibili,
            "dimmer_compatibili": dimmer_compatibili,
            "calcolo_alimentatori": {
                "possibile": calcolo_alimentatori_possibile,
                "potenza_per_metro": potenza_per_metro,
                "voltaggio": voltaggio_strip,
                "info": "Inserisci metri per calcolare alimentatori necessari" if calcolo_alimentatori_possibile else "Dati insufficienti per calcolo alimentatori"
            },
            "debug": {
                "voltaggio_strip": input_volt_strip_float,
                "temperatura_colore": temp_colore_strip,
                "categoria_canali_strip": categoria_canali_strip,
                "num_dimmer_compatibili": len(dimmer_compatibili),
                "num_profili_compatibili": len(profili_compatibili)
            }
        })

    # --- RICERCA PROFILO (rimane uguale) ---
    profilo = next((p for p in profili_data if p['Codice'].strip().upper() == codice), None)
    if profilo:
        print(f"✅ Profilo trovato: {profilo['Codice']}")
        
        larghezza_profilo = profilo_larghezze.get(codice)
        if larghezza_profilo is None:
            return jsonify({"error": "Larghezza profilo non trovata"}), 404

        strip_compatibili = [
            s for s in strip_data
            if s.get('Codice') and
            strip_larghezze.get(s['Codice'].strip().upper()) is not None and
            strip_larghezze[s['Codice'].strip().upper()] <= larghezza_profilo
        ]

        profilo_con_dettagli = profilo.copy()
        profilo_con_dettagli['dettagli_completi'] = prepara_dettagli_profilo(profilo)

        print(f"🎯 Profilo: {len(strip_compatibili)} strip compatibili")

        return jsonify({
            "tipo": "profilo",
            "profilo": profilo_con_dettagli,
            "strip_compatibili": strip_compatibili,
            "debug": {
                "larghezza_profilo": larghezza_profilo,
                "num_strip_compatibili": len(strip_compatibili)
            }
        })

    # --- RICERCA DIMMER (aggiornata con nuova logica) ---
    dimmer = next((d for d in dimmer_data if d['Codice'].strip().upper() == codice), None)
    if dimmer:
        print(f"✅ Dimmer trovato: {dimmer['Codice']}")
        
        min_v, max_v = dimmer_voltaggi.get(codice, (None, None))
        if min_v is None or max_v is None:
            return jsonify({"error": "Voltaggio dimmer non trovato"}), 404

        categoria_canali_dimmer = determina_categoria_canali_dimmer(dimmer)

        print(f"🔌 Dimmer - Range: [{min_v}-{max_v}V], Categoria: {categoria_canali_dimmer}")

        # Trova le strip compatibili con NUOVA LOGICA
        strip_compatibili = []
        for s in strip_data:
            if not s.get('Codice'):
                continue

            input_volt_strip = estrai_voltaggio_singolo(s.get('Input Volt', ''))
            if input_volt_strip is None:
                continue

            categoria_canali_strip = determina_categoria_canali_strip(s)
            temp_colore_strip = estrai_temperatura_colore(s)

            # Controlli di compatibilità
            is_volt_compatibile = min_v <= input_volt_strip <= max_v
            
            # Compatibilità basata su categoria canali derivata da temperatura colore
            is_canali_compatibile = (
                categoria_canali_dimmer is not None and 
                categoria_canali_strip is not None and 
                categoria_canali_dimmer == categoria_canali_strip
            )

            if is_volt_compatibile and is_canali_compatibile:
                strip_compatibili.append(s)
                print(f"  ✅ Strip compatibile: {s['Codice']} ({input_volt_strip}V, {temp_colore_strip}K, {categoria_canali_strip})")
            else:
                motivi = []
                if not is_volt_compatibile:
                    motivi.append(f"voltaggio ({input_volt_strip}V)")
                if not is_canali_compatibile:
                    motivi.append(f"categoria canali ({categoria_canali_strip} vs {categoria_canali_dimmer})")
                print(f"  ❌ Strip NON compatibile: {s['Codice']} - {', '.join(motivi)}")

        return jsonify({
            "tipo": "dimmer",
            "dimmer": dimmer,
            "strip_compatibili": strip_compatibili,
            "debug": {
                "voltaggio_dimmer": [min_v, max_v],
                "categoria_canali_dimmer": categoria_canali_dimmer,
                "num_strip_compatibili": len(strip_compatibili)
            }
        })

# --- RICERCA ALIMENTATORE (COMPLETA) ---

def calcola_compatibilita_strip(strip, corrente_alimentatore):
    """
    Calcola la compatibilità di una strip LED con un alimentatore.
    
    Args:
        strip: Dizionario con i dati della strip
        corrente_alimentatore: Corrente massima dell'alimentatore in Ampere
    
    Returns:
        dict o None: Informazioni sulla strip con compatibilità calcolata
    """
    if not strip.get('Codice'):
        return None
        
    potenza_per_metro = estrai_potenza_strip(strip.get('Potenza', ''))
    voltaggio_strip = estrai_voltaggio_strip(strip.get('Input Volt', ''))
    
    if potenza_per_metro is None or voltaggio_strip is None:
        return None
    
    # Evita divisione per zero
    if voltaggio_strip == 0:
        return None
    
    # Calcola ampere per metro e metri massimi supportabili
    ampere_per_metro = potenza_per_metro / voltaggio_strip
    
    # Margine di sicurezza del 20% (fattore 1.2)
    MARGINE_SICUREZZA = 1.2
    metri_max = corrente_alimentatore / (ampere_per_metro * MARGINE_SICUREZZA)
    
    # Considera compatibile solo se supporta almeno 10cm
    LUNGHEZZA_MINIMA = 0.1  # 10cm
    if metri_max < LUNGHEZZA_MINIMA:
        return None
    
    # Prepara le informazioni della strip compatibile
    strip_info = strip.copy()
    strip_info.update({
        'metri_max_supportati': round(metri_max, 2),
        'ampere_per_metro': round(ampere_per_metro, 3),
        'potenza_per_metro': round(potenza_per_metro, 2),
        'voltaggio': voltaggio_strip
    })
    
    return strip_info


def trova_strip_compatibili(corrente_alimentatore, strip_data):
    """
    Trova tutte le strip LED compatibili con un alimentatore.
    
    Args:
        corrente_alimentatore: Corrente dell'alimentatore in Ampere
        strip_data: Lista di tutte le strip disponibili
    
    Returns:
        list: Lista delle strip compatibili con informazioni aggiuntive
    """
    strip_compatibili = []
    
    for strip in strip_data:
        strip_compatibile = calcola_compatibilita_strip(strip, corrente_alimentatore)
        if strip_compatibile:
            strip_compatibili.append(strip_compatibile)
    
    # Ordina per metri massimi supportati (decrescente)
    strip_compatibili.sort(key=lambda x: x['metri_max_supportati'], reverse=True)
    
    return strip_compatibili


def ricerca_alimentatore(codice, alimentatori_data, strip_data):
    """
    Funzione principale per la ricerca di un alimentatore e strip compatibili.
    
    Args:
        codice: Codice dell'alimentatore da cercare
        alimentatori_data: Lista di tutti gli alimentatori disponibili
        strip_data: Lista di tutte le strip disponibili
    
    Returns:
        tuple: (response_data, status_code)
    """
    # Cerca alimentatore per codice
    alimentatore = next(
        (a for a in alimentatori_data if a.get('codice', '').strip().upper() == codice.upper()), 
        None
    )
    
    if not alimentatore:
        return {
            "error": "Alimentatore non trovato",
            "codice": codice
        }, 404
    
    # Verifica corrente alimentatore
    corrente_alimentatore = alimentatore.get('corrente_A')
    
    if corrente_alimentatore is None:
        return {
            "error": "Corrente alimentatore non trovata",
            "codice": codice
        }, 404
    
    # Validazione corrente
    if corrente_alimentatore <= 0:
        return {
            "error": "Corrente alimentatore non valida",
            "corrente": corrente_alimentatore,
            "codice": codice
        }, 400
    
    # Trova strip compatibili
    strip_compatibili = trova_strip_compatibili(corrente_alimentatore, strip_data)
    
    # Prepara la risposta
    risposta = {
        "tipo": "alimentatore",
        "alimentatore": {
            "codice": alimentatore.get("codice", ""),
            "potenza_uscita": alimentatore.get("potenza_W", 0),
            "tensione_uscita": alimentatore.get("tensione_V", 0),
            "corrente_uscita": alimentatore.get("corrente_A", 0),
            "descrizione": alimentatore.get("nome", ""),
            "tipo_corrente": alimentatore.get("tipo_corrente", "")
        },
        "strip_compatibili": strip_compatibili,
        "statistiche": {
            "num_strip_compatibili": len(strip_compatibili),
            "metri_max_globale": max(
                [s['metri_max_supportati'] for s in strip_compatibili], 
                default=0
            ),
            "corrente_alimentatore": corrente_alimentatore
        }
    }
    
    return risposta, 200


# Esempio di utilizzo nell'endpoint Flask
@app.route('/api/prodotto/<codice>', methods=['GET'])
def get_prodotto(codice):
    """
    Endpoint per la ricerca di prodotti (alimentatori e strip LED).
    """
    try:
        # Normalizza il codice
        codice = codice.strip().upper()
        
        if not codice:
            return jsonify({"error": "Codice prodotto non fornito"}), 400
        
        # Cerca prima negli alimentatori
        response_data, status_code = ricerca_alimentatore(codice, alimentatori_data, strip_data)
        
        if status_code == 200:
            return jsonify(response_data), status_code
        
        # Se non è un alimentatore, qui potresti aggiungere la ricerca per altri tipi di prodotti
        # Per esempio: ricerca_strip(codice, strip_data)
        
        # Se nessun prodotto trovato
        return jsonify({
            "error": "Nessun prodotto trovato",
            "codice": codice
        }), 404
        
    except Exception as e:
        return jsonify({
            "error": "Errore interno del server",
            "details": str(e) if app.debug else None
        }), 500


def estrai_voltaggio_strip(voltaggio_str):
    """
    Estrae il valore di voltaggio da una stringa.
    
    Args:
        voltaggio_str: Stringa contenente il voltaggio (es. "12V", "24V DC")
    
    Returns:
        float o None: Valore del voltaggio
    """
    if not voltaggio_str:
        return None
    
    try:
        # Rimuove "V", "DC", "AC" e altri caratteri non numerici
        numero = re.search(r'(\d+\.?\d*)', str(voltaggio_str))
        if numero:
            return float(numero.group(1))
    except (ValueError, AttributeError):
        pass
    
    return None
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
    app.config['DEBUG'] = True
    

