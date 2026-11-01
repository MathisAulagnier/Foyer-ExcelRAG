"""
Application Web Streamlit : Agent d'analyse Excel & DuckDB
----------------------------------------------------------
Cette application permet de :
1. Déposer n'importe quel fichier Excel (.xlsx).
2. Détecter dynamiquement les onglets et charger les tables dans DuckDB.
3. Valider visuellement le schéma de la base (colonnes, types, échantillons).
4. Poser des questions en langage naturel et obtenir :
   - La réponse exacte calculée en SQL
   - La requête SQL en toute transparence
   - Un graphique automatique si les données s'y prêtent
"""

import os
import re
import json
import time
from pathlib import Path
import pandas as pd
import duckdb
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv
from openai import OpenAI

# 1. Configuration de la page Streamlit
st.set_page_config(
    page_title="Excel AI Analyst | Foyer",
    page_icon="📊",
    layout="wide"
)

# 2. Chargement des variables d'environnement (.env)
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_EXCEL = BASE_DIR / "data" / "frtr_0021_insurance-claims.xlsx"

def get_default_api_key() -> str:
    """Récupère la clé API par défaut (st.secrets ou .env)."""
    try:
        if "OPENROUTER_API_KEY" in st.secrets:
            return st.secrets["OPENROUTER_API_KEY"]
    except Exception:
        pass
    return os.getenv("OPENROUTER_API_KEY", "")

def get_default_model() -> str:
    """Récupère le modèle par défaut (st.secrets ou .env)."""
    try:
        if "OPENROUTER_MODEL" in st.secrets:
            return st.secrets["OPENROUTER_MODEL"]
    except Exception:
        pass
    return os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free")

# -------------------------------------------------------------
# Fonctions de traitement des données Excel et DuckDB
# -------------------------------------------------------------

def load_excel_to_duckdb(file_source) -> tuple[duckdb.DuckDBPyConnection, dict]:
    """
    Charge chaque onglet d'un classeur Excel dans une table DuckDB.
    Détecte automatiquement si la 1ère ligne est une note ou le vrai header.
    """
    con = duckdb.connect(database=":memory:")
    excel_file = pd.ExcelFile(file_source)
    tables_summary = {}

    for sheet_name in excel_file.sheet_names:
        # Nettoyage du nom de la table pour SQL (sans espaces ni caractères spéciaux)
        table_name = re.sub(r"[^a-zA-Z0-9_]", "_", sheet_name).lower()

        # Lecture d'un échantillon pour inspecter les en-têtes
        sample_df = pd.read_excel(file_source, sheet_name=sheet_name, nrows=3, header=None)
        
        # Heuristique : si la ligne 0 a beaucoup de valeurs nulles ou une seule cellule remplie,
        # le vrai header se trouve en ligne 1 (comme dans notre benchmark d'assurance)
        header_row = 0
        if len(sample_df) > 1 and sample_df.iloc[0].count() == 1 and sample_df.iloc[1].count() > 1:
            header_row = 1
            
        df = pd.read_excel(file_source, sheet_name=sheet_name, header=header_row)

        # Nettoyage des colonnes : suppression des 'Unnamed:' et des espaces
        valid_cols = [c for c in df.columns if not str(c).startswith("Unnamed:")]
        df = df[valid_cols]
        df.columns = [str(c).strip().replace(" ", "_") for c in df.columns]

        # Enregistrement dans DuckDB
        con.register(table_name, df)
        tables_summary[table_name] = {
            "sheet_original": sheet_name,
            "row_count": len(df),
            "columns": list(df.columns),
            "df_sample": df.head(3)
        }

    return con, tables_summary


def generate_schema_text(con: duckdb.DuckDBPyConnection) -> str:
    """Génère la description textuelle du schéma pour le prompt LLM."""
    schema_parts = []
    tables = [row[0] for row in con.execute("SHOW TABLES").fetchall()]
    
    for t in tables:
        schema_parts.append(f"### Table : `{t}`")
        cols = con.execute(f"DESCRIBE {t}").fetchall()
        schema_parts.append("Colonnes : " + ", ".join([f"`{c[0]}` ({c[1]})" for c in cols]))
        
        sample_df = con.execute(f"SELECT * FROM {t} LIMIT 2").df()
        schema_parts.append("Exemples :")
        schema_parts.append(sample_df.to_string(index=False))
        schema_parts.append("")
        
    return "\n".join(schema_parts)


def call_llm(messages: list) -> str:
    """Appel OpenRouter avec clé et modèle configurables par l'utilisateur."""
    api_key = st.session_state.get("custom_api_key", "").strip()
    model_name = st.session_state.get("custom_model", "nvidia/nemotron-3.5-lightning:free").strip()
    
    if not api_key:
        raise ValueError("Veuillez renseigner votre clé API OpenRouter dans la barre latérale gauche pour activer l'agent.")
        
    ai_client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    for attempt in range(4):
        try:
            response = ai_client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.1,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            if "429" in str(e) and attempt < 3:
                time.sleep((attempt + 1) * 3)
            else:
                raise e
    return ""


def extract_sql(content: str) -> str:
    """Extrait le code SQL dans les balises ```sql ... ```."""
    sql_match = re.search(r"```(?:sql)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    return sql_match.group(1).strip() if sql_match else content.strip()


def run_agent(question: str, con: duckdb.DuckDBPyConnection, schema: str, max_retries: int = 2):
    """
    Agent SQL avec boucle d'auto-correction.
    Retourne (succès, sql, dataframe, historique_logs).
    """
    system_prompt = f"""Tu es un analyste expert SQL DuckDB.
Ton rôle est de traduire la question de l'utilisateur en une requête SQL SELECT valide et optimisée.

Schéma des tables disponibles :
{schema}

Règles impératives :
1. Rédige UNIQUEMENT du SQL de lecture (SELECT ou WITH).
2. Utilise strictement les noms de tables et colonnes du schéma.
3. Encadre impérativement ton code SQL dans un bloc ```sql ... ```.
"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question}
    ]
    
    logs = []
    
    for attempt in range(max_retries + 1):
        content = call_llm(messages)
        sql = extract_sql(content)
        logs.append(f"Tentative {attempt + 1} - SQL proposé : \n{sql}")
        
        # Contrôle de sécurité
        cleaned_sql = sql.strip().upper()
        if not cleaned_sql.startswith("SELECT") and not cleaned_sql.startswith("WITH"):
            err_msg = "Sécurité : Seules les requêtes de lecture (SELECT / WITH) sont autorisées."
            logs.append(f"❌ {err_msg}")
            return False, sql, None, logs

        try:
            df_result = con.execute(sql).df()
            logs.append("✅ Exécution DuckDB réussie !")
            return True, sql, df_result, logs
            
        except Exception as sql_err:
            logs.append(f"⚠️ Erreur DuckDB : {sql_err}")
            if attempt < max_retries:
                logs.append("🔄 Renvoi de l'erreur au LLM pour auto-correction...")
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": f"Erreur DuckDB lors de l'exécution :\n{sql_err}\n\nCorrige ta requête SQL dans un bloc ```sql ... ```."
                })
                time.sleep(1)
            else:
                logs.append("❌ Échec après épuisement des tentatives.")
                return False, sql, None, logs

    return False, "", None, logs


def generate_chart_config(question: str, df: pd.DataFrame) -> tuple[dict, list]:
    """
    Étape de décision de visualisation par le LLM :
    L'agent analyse la question et les données retournées pour décider si un graphique
    est pertinent et quels axes X, Y et couleur choisir.
    Retourne (config_dict, logs_list).
    """
    chart_logs = []
    
    if df.empty:
        chart_logs.append("Le DataFrame est vide : aucun graphique généré.")
        return {"should_plot": False}, chart_logs
        
    if len(df) == 1 and len(df.columns) == 1:
        chart_logs.append("Résultat scalaire unique : le tableau suffit, pas de graphique pertinent.")
        return {"should_plot": False}, chart_logs

    chart_logs.append(f"Colonnes analysées par l'agent : {list(df.columns)}")
    chart_logs.append(f"Nombre de lignes transmises : {len(df)}")
    
    csv_sample = df.head(5).to_csv(index=False)
    chart_logs.append("Extrait des données envoyé dans le prompt :\n" + csv_sample)
    
    prompt = f"""Tu es un expert en dataviz avec Plotly.
L'utilisateur a posé la question : "{question}"
Voici les colonnes et les 5 premières lignes du tableau résultat obtenu :
Colonnes : {list(df.columns)}
Données d'échantillon :
{csv_sample}

Détermine la meilleure configuration Plotly Express pour représenter ces données.
Règles impératives :
1. "x": choisis la colonne d'étiquettes / catégories (ex: 'LOB' ou 'State').
2. "y": choisis la ou les colonnes numériques. 
   - S'il y a une seule mesure : "y": "nom_colonne"
   - S'il y a plusieurs colonnes de métriques à comparer (ex: 'claims_2024' et 'claims_2025') : "y": ["claims_2024", "claims_2025"] avec "barmode": "group" et "color": null.
3. Si une colonne de groupement existe en ligne (ex: colonne 'annee' avec les valeurs 2024 et 2025 dans les lignes) : utilise "color": "annee" avec "barmode": "group".
4. "chart_type": "bar", "line", ou "pie".

Réponds UNIQUEMENT par un objet JSON valide :
{{
  "should_plot": true,
  "chart_type": "bar",
  "x": "nom_colonne_axe_x",
  "y": "colonne_ou_liste_de_colonnes",
  "color": "colonne_groupement_ou_null",
  "barmode": "group",
  "title": "Titre explicatif du graphique"
}}
"""
    try:
        content = call_llm([{"role": "user", "content": prompt}])
        chart_logs.append("Réponse brute du modèle LLM :\n" + content)
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            config = json.loads(json_match.group(0))
            if config.get("should_plot"):
                chart_logs.append(
                    f"Configuration retenue : Type='{config.get('chart_type')}', "
                    f"Axe X='{config.get('x')}', Axe Y='{config.get('y')}', "
                    f"Couleur='{config.get('color')}', Barmode='{config.get('barmode')}'"
                )
            else:
                chart_logs.append("L'agent a jugé qu'un graphique n'était pas pertinent pour ce type de données.")
            return config, chart_logs
    except Exception as e:
        chart_logs.append(f"⚠️ Erreur lors de la génération du graphique : {e}")
        
    return {"should_plot": False}, chart_logs


def render_ai_chart(df: pd.DataFrame, config: dict):
    """Génère le graphique Plotly selon les spécifications choisies par le LLM."""
    if not config.get("should_plot"):
        return

    chart_type = config.get("chart_type", "bar")
    x = config.get("x")
    y = config.get("y")
    color = config.get("color")
    title = config.get("title", "")
    barmode = config.get("barmode", "group")

    # Validation de l'existence des colonnes (y peut être une liste ou une chaîne)
    if x not in df.columns:
        st.warning(f"Colonne X '{x}' non trouvée dans les données.")
        return
        
    if isinstance(y, list):
        missing_y = [col for col in y if col not in df.columns]
        if missing_y:
            st.warning(f"Colonnes Y non trouvées : {missing_y}")
            return
    elif isinstance(y, str):
        if y not in df.columns:
            st.warning(f"Colonne Y '{y}' non trouvée dans les données.")
            return
    else:
        return

    if color and color not in df.columns:
        color = None

    plot_df = df.copy()
    if color and color in plot_df.columns:
        plot_df[color] = plot_df[color].astype(str)

    try:
        if chart_type == "bar":
            fig = px.bar(
                plot_df, 
                x=x, 
                y=y, 
                color=color, 
                barmode=barmode,
                title=title, 
                text_auto=True
            )
        elif chart_type == "line":
            fig = px.line(plot_df, x=x, y=y, color=color, title=title, markers=True)
        elif chart_type == "pie":
            fig = px.pie(plot_df, names=x, values=y, title=title)
        else:
            return

        fig.update_layout(legend_title_text=color if color else "")
        st.plotly_chart(fig, use_container_width=True)
    except Exception as err:
        st.error(f"Erreur d'affichage Plotly : {err}")


# -------------------------------------------------------------
# Interface Utilisateur Streamlit
# -------------------------------------------------------------

st.title("📊 Assistant IA Tabulaire (Excel & DuckDB)")
active_model = st.session_state.get("custom_model", get_default_model())
st.caption(f"Moteur SQL in-memory : **DuckDB** | LLM : `{active_model}` via OpenRouter")

# Barre latérale : Clé API & Gestion du fichier Excel
with st.sidebar:
    st.header("🔑 Configuration IA")
    api_key_input = st.text_input(
        "Clé API OpenRouter",
        value=st.session_state.get("custom_api_key", get_default_api_key()),
        type="password",
        help="Votre clé (sk-or-...). Elle reste dans votre session locale et n'est jamais sauvegardée sur disque."
    )
    st.session_state["custom_api_key"] = api_key_input
    
    model_input = st.text_input(
        "Modèle LLM",
        value=st.session_state.get("custom_model", get_default_model()),
        help="Ex: nvidia/nemotron-3.5-lightning:free, meta-llama/llama-3.3-70b-instruct:free, openai/gpt-4o-mini"
    )
    st.session_state["custom_model"] = model_input
    
    if not api_key_input:
        st.info("💡 Clé manquante ? Créez une clé gratuitement sur [openrouter.ai/keys](https://openrouter.ai/keys).")
        
    st.divider()
    st.header("📁 Source de données")
    uploaded_file = st.file_uploader(
        "Déposer un fichier Excel (.xlsx, .xls)",
        type=["xlsx", "xls"],
        help="Déposez votre propre fichier Excel ou utilisez le fichier d'assurance par défaut."
    )
    
    file_to_load = uploaded_file if uploaded_file is not None else DEFAULT_EXCEL
    
    if uploaded_file is None:
        st.info("💡 Fichier actif : **Benchmark Sinistres Assurance (Foyer)**")

# Avertissement si aucune clé n'est configurée
if not st.session_state.get("custom_api_key"):
    st.warning("👈 **Pour démarrer :** Renseignez votre clé API OpenRouter dans la barre latérale gauche.")

# Chargement et initialisation de la base
if "con" not in st.session_state or st.sidebar.button("🔄 Recharger / Réinitialiser"):
    with st.spinner("Chargement des tables dans DuckDB..."):
        con, summary = load_excel_to_duckdb(file_to_load)
        st.session_state["con"] = con
        st.session_state["summary"] = summary
        st.session_state["schema"] = generate_schema_text(con)
        st.session_state["file_name"] = uploaded_file.name if uploaded_file else "frtr_0021_insurance-claims.xlsx"
        if "active_result" in st.session_state:
            del st.session_state["active_result"]

con = st.session_state["con"]
summary = st.session_state["summary"]
schema = st.session_state["schema"]

# Validation et affichage visuel du schéma
with st.expander("📋 Schéma & Tables détectées (Cliquez pour valider)", expanded=False):
    st.write(f"**Fichier actif :** `{st.session_state['file_name']}`")
    
    tabs = st.tabs([f"Table: {t}" for t in summary.keys()])
    for idx, (t_name, info) in enumerate(summary.items()):
        with tabs[idx]:
            st.write(f"- **Onglet d'origine :** `{info['sheet_original']}`")
            st.write(f"- **Nombre de lignes :** `{info['row_count']:,}`")
            st.write(f"- **Colonnes :** `{', '.join(info['columns'])}`")
            st.write("**Échantillon des premières lignes :**")
            st.dataframe(info["df_sample"], use_container_width=True)

# Zone de questions
st.subheader("💬 Interroger les données en langage naturel")

# Suggestions rapides adaptées si c'est le fichier assurance
if "claims" in summary:
    st.write("Suggestions d'analyse :")
    cols = st.columns(3)
    if cols[0].button("🚗 Montant moyen payé (Auto)"):
        st.session_state["user_query"] = "Quel est le montant moyen payé pour les sinistres Auto ?"
    if cols[1].button("⚠️ Top 5 des sinistres ouverts"):
        st.session_state["user_query"] = "Donne-moi les 5 sinistres ouverts avec le montant payé le plus élevé"
    if cols[2].button("🔗 Jointure sinistre & règlement"):
        st.session_state["user_query"] = "Pour la police POL-028810, donne-moi le statut du sinistre et le montant réglé"

query = st.chat_input("Ex: Quel est le total des sinistres par statut et par branche ?")

if "user_query" in st.session_state:
    query = st.session_state.pop("user_query")

if query:
    with st.spinner("L'agent analyse le schéma et construit la requête SQL..."):
        success, sql, df_result, logs = run_agent(query, con, schema)
    st.session_state["active_result"] = {
        "query": query,
        "success": success,
        "sql": sql,
        "df": df_result,
        "logs": logs,
        "chart_config": None,
        "chart_logs": None
    }

# Affichage du résultat actif
if "active_result" in st.session_state:
    active = st.session_state["active_result"]
    st.chat_message("user").write(active["query"])
    
    with st.chat_message("assistant"):
        if active["success"] and active["df"] is not None:
            df_result = active["df"]
            
            # 1. Message de confirmation
            st.success(f"Calcul terminé en quelques millisecondes sur DuckDB ({len(df_result)} résultat(s)).")
            
            # 2. Affichage des données
            st.dataframe(df_result, use_container_width=True)
            
            # 3. Actions : Téléchargement CSV + Bouton graphique facultatif
            col_csv, col_chart = st.columns([1, 2])
            with col_csv:
                csv_bytes = df_result.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Télécharger le résultat (CSV)",
                    data=csv_bytes,
                    file_name="resultat_analyse.csv",
                    mime="text/csv",
                    key="download_csv_btn"
                )
            with col_chart:
                if st.button("📊 Générer un graphique intelligent (Agent Dataviz)", key="generate_chart_btn"):
                    with st.spinner("L'agent Dataviz choisit la meilleure visualisation..."):
                        c_config, c_logs = generate_chart_config(active["query"], df_result)
                        active["chart_config"] = c_config
                        active["chart_logs"] = c_logs
            
            # 4. Transparence technique (Accordéon SQL)
            with st.expander("🔍 Voir la requête SQL exécutée & le journal de calcul DuckDB"):
                st.code(active["sql"], language="sql")
                st.caption("Journal de réflexion de l'Agent SQL :")
                for log in active["logs"]:
                    st.text(log)
                    
            # 5. Affichage du graphique si déclenché à la demande
            if active.get("chart_config") is not None:
                c_config = active["chart_config"]
                c_logs = active.get("chart_logs", [])
                
                if c_config.get("should_plot"):
                    st.markdown("---")
                    st.subheader("📈 Visualisation générée par l'Agent Dataviz")
                    render_ai_chart(df_result, c_config)
                else:
                    st.info("ℹ️ L'agent a analysé les données et a déterminé qu'un graphique n'apporterait pas de valeur pour ce résultat.")
                    
                with st.expander("🎨 Voir les logs de l'Agent Dataviz (Prompt & JSON)"):
                    for clog in c_logs:
                        st.text(clog)

        else:
            st.error("L'agent n'a pas pu exécuter la requête avec succès.")
            with st.expander("Détails des erreurs et tentatives"):
                for log in active["logs"]:
                    st.text(log)
