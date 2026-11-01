"""
Étape 3 : Traduction Langage Naturel -> SQL avec OpenRouter et DuckDB
---------------------------------------------------------------------
Ce script a pour but de :
1. Charger les variables d'environnement depuis le fichier .env.
2. Construire un prompt système strict pour forcer le LLM à générer du SQL DuckDB valide.
3. Appeler le LLM choisi via l'API OpenRouter.
4. Exécuter la requête SQL générée directement dans DuckDB.
5. Formater et afficher le résultat pour l'utilisateur.
"""

import os
import re
import time
from pathlib import Path
import pandas as pd
import duckdb
from dotenv import load_dotenv
from openai import OpenAI

from step2_schema_generator import get_database_schema

# 1. Chargement des clés et paramètres depuis le fichier .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL_NAME = os.getenv("OPENROUTER_MODEL", "xingchen-agi/xing4.0-29b-a4b:free")

if not API_KEY:
    raise ValueError("La variable OPENROUTER_API_KEY est manquante dans le fichier .env")

# 2. Initialisation du client OpenAI configuré pour OpenRouter
# OpenRouter utilise la même interface que l'API OpenAI, il suffit de changer 'base_url'
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=API_KEY,
)

def init_duckdb() -> duckdb.DuckDBPyConnection:
    """Charge les données Excel dans DuckDB en mémoire."""
    excel_path = BASE_DIR / "data" / "frtr_0021_insurance-claims.xlsx"
    
    df_claims = pd.read_excel(excel_path, sheet_name="Claims", header=1)
    df_payments = pd.read_excel(excel_path, sheet_name="Payments", header=1)
    df_summary = pd.read_excel(excel_path, sheet_name="LOB_Summary", header=1)

    # Nettoyage des colonnes Unnamed
    df_claims = df_claims[[c for c in df_claims.columns if not str(c).startswith("Unnamed:")]]
    df_payments = df_payments[[c for c in df_payments.columns if not str(c).startswith("Unnamed:")]]
    df_summary = df_summary[[c for c in df_summary.columns if not str(c).startswith("Unnamed:")]]

    con = duckdb.connect(database=":memory:")
    con.register("claims", df_claims)
    con.register("payments", df_payments)
    con.register("lob_summary", df_summary)
    return con

def call_llm_with_retry(messages: list) -> str:
    """Appelle le LLM avec gestion des éventuelles limites de débit (HTTP 429)."""
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.1,  # Déterministe pour le code SQL
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                print("   [Info API] Limite temporaire atteinte, pause de 3s...")
                time.sleep(3)
            else:
                raise e
    return ""

def extract_sql(content: str) -> str:
    """Extrait le code SQL situé entre les balises Markdown ```sql ... ```."""
    sql_match = re.search(r"```(?:sql)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
    if sql_match:
        return sql_match.group(1).strip()
    return content.strip()

def execute_safely(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    """Vérifie la sécurité élémentaire et exécute la requête dans DuckDB."""
    cleaned_sql = sql.strip().upper()
    interdits = ["DROP ", "DELETE ", "INSERT ", "UPDATE ", "ALTER ", "ATTACH "]
    for mot in interdits:
        if mot in cleaned_sql:
            raise PermissionError(f"Action interdite détectée : {mot}")
            
    if not cleaned_sql.startswith("SELECT") and not cleaned_sql.startswith("WITH"):
        raise PermissionError("Seules les requêtes de lecture (SELECT / WITH) sont autorisées.")
        
    return con.execute(sql).df()

def ask(question: str, con: duckdb.DuckDBPyConnection, schema: str, max_retries: int = 2):
    """
    Boucle Agentique complète avec Auto-Correction (Self-Correction) :
    1. Le LLM génère une requête SQL.
    2. DuckDB tente d'exécuter la requête.
    3. Si DuckDB renvoie une erreur (colonne inconnue, syntaxe...), on renvoie l'erreur
       au LLM pour qu'il s'auto-corrige (jusqu'à max_retries).
    """
    print(f"\n=======================================================")
    print(f" QUESTION MÉTIER : \"{question}\"")
    print(f"=======================================================")

    system_prompt = f"""Tu es un analyste expert SQL DuckDB pour le secteur de l'assurance.
Voici le schéma des tables et des exemples de données :
{schema}

Règles :
1. Rédige UNIQUEMENT du SQL de lecture (SELECT ou WITH).
2. Utilise strictement les colonnes du schéma.
3. Encadre impérativement ton code SQL dans un bloc ```sql ... ```.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question}
    ]

    for attempt in range(max_retries + 1):
        # 1. Appel du LLM pour obtenir la requête SQL
        content = call_llm_with_retry(messages)
        sql = extract_sql(content)
        
        tentative_label = f" (Tentative {attempt + 1})" if attempt > 0 else ""
        print(f"\n[1] Requête SQL proposée par l'IA{tentative_label} :\n{sql}\n")

        # 2. Tentative d'exécution dans DuckDB
        try:
            df_result = execute_safely(con, sql)
            
            # Succès !
            print("[2] Résultat de l'exécution (DuckDB) :")
            if df_result.empty:
                print("-> Aucun résultat trouvé (table vide pour ces filtres).")
            else:
                print(df_result.to_string(index=False))
            break  # On sort de la boucle avec succès

        except Exception as sql_err:
            print(f"⚠️ Erreur DuckDB détectée : {sql_err}")
            
            if attempt < max_retries:
                print("🔄 Renvoi de l'erreur au LLM pour auto-correction...")
                # On ajoute au fil de discussion la requête ratée et le message d'erreur précis
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        f"L'exécution de ta requête a échoué avec l'erreur suivante dans DuckDB :\n"
                        f"{sql_err}\n\n"
                        f"Analyse cette erreur et fournis une requête SQL corrigée dans un bloc ```sql ... ```."
                    )
                })
                time.sleep(2)
            else:
                print(f"❌ Échec après {max_retries + 1} tentatives.")

    time.sleep(2)  # Temporisation de courtoisie entre questions


if __name__ == "__main__":
    print(f"Modèle sélectionné : {MODEL_NAME}")
    print("Initialisation de DuckDB...")
    con = init_duckdb()
    
    print("Extraction du schéma...")
    schema = get_database_schema(con)
    
    # Test avec 3 questions concrètes d'assurance
    
    # Question 1 : issue directement de l'onglet 'Questions' du benchmark Excel
    ask("Quel est le montant moyen payé pour les sinistres Auto ?", con, schema)
    
    # Question 2 : Filtre et tri
    ask("Quels sont les 3 sinistres ouverts avec le montant payé le plus élevé ?", con, schema)
    
    # Question 3 : Jointure relationnelle entre Claims et Payments
    ask("Pour la police POL-028810, donne-moi le statut du sinistre et le montant réglé dans les paiements.", con, schema)
