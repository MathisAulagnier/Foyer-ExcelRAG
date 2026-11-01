"""
Étape 2 : Génération automatique du schéma de la base pour le LLM
-----------------------------------------------------------------
Ce script a pour but de :
1. Inspecter la structure de chaque table présente dans DuckDB.
2. Extraire les colonnes, leurs types de données.
3. Extraire 2 ou 3 lignes d'exemple réelles pour que le LLM voie le format
   des données (ex: format de PolicyID = 'POL-XXXXX', valeurs de Status = 'Open'/'Closed').
4. Formater tout cela dans un texte clair et compact (moins de 500 tokens).
"""

from pathlib import Path
import pandas as pd
import duckdb

def get_database_schema(con: duckdb.DuckDBPyConnection) -> str:
    """
    Inspecte la connexion DuckDB et produit une description textuelle
    du schéma, optimisée pour être injectée dans le prompt d'un LLM.
    """
    schema_parts = []
    
    # 1. Récupérer la liste des tables
    tables = [row[0] for row in con.execute("SHOW TABLES").fetchall()]
    
    for table_name in tables:
        schema_parts.append(f"### Table : `{table_name}`")
        
        # 2. Obtenir la description des colonnes (nom, type)
        # La commande DuckDB 'DESCRIBE <table>' renvoie [column_name, column_type, null, key, default, extra]
        columns_info = con.execute(f"DESCRIBE {table_name}").fetchall()
        col_descriptions = [f"  - `{col[0]}` ({col[1]})" for col in columns_info]
        schema_parts.append("Colonnes :")
        schema_parts.extend(col_descriptions)
        
        # 3. Récupérer 2 lignes d'exemple pour donner du contexte visuel au LLM
        sample_df = con.execute(f"SELECT * FROM {table_name} LIMIT 2").df()
        schema_parts.append("Exemples de lignes :")
        # On convertit les exemples en format tabulaire compact sans dépendance externe
        schema_parts.append(sample_df.to_string(index=False))
        
        # 4. Spécificité pour les colonnes catégorielles :
        # Connaître les valeurs exactes évite au LLM d'inventer 'Terminé' au lieu de 'Closed'
        if table_name == "claims":
            lobs = [row[0] for row in con.execute("SELECT DISTINCT LOB FROM claims").fetchall()]
            statuses = [row[0] for row in con.execute("SELECT DISTINCT Status FROM claims").fetchall()]
            schema_parts.append(f"Valeurs possibles pour `LOB` : {lobs}")
            schema_parts.append(f"Valeurs possibles pour `Status` : {statuses}")
            
        schema_parts.append("") # Ligne vide pour aérer
        
    return "\n".join(schema_parts)


if __name__ == "__main__":
    # Chargement initial (identique à l'étape 1)
    BASE_DIR = Path(__file__).resolve().parent
    EXCEL_PATH = BASE_DIR / "data" / "frtr_0021_insurance-claims.xlsx"

    print("Chargement des données dans DuckDB...")
    df_claims = pd.read_excel(EXCEL_PATH, sheet_name="Claims", header=1)
    df_payments = pd.read_excel(EXCEL_PATH, sheet_name="Payments", header=1)
    df_summary = pd.read_excel(EXCEL_PATH, sheet_name="LOB_Summary", header=1)

    # Nettoyage des colonnes fantômes d'Excel (ex: 'Unnamed: 4' qui ne contiennent que des NaN)
    df_claims = df_claims[[c for c in df_claims.columns if not str(c).startswith("Unnamed:")]]
    df_payments = df_payments[[c for c in df_payments.columns if not str(c).startswith("Unnamed:")]]
    df_summary = df_summary[[c for c in df_summary.columns if not str(c).startswith("Unnamed:")]]

    con = duckdb.connect(database=":memory:")
    con.register("claims", df_claims)
    con.register("payments", df_payments)
    con.register("lob_summary", df_summary)

    # Génération du schéma
    print("\n--- Schéma généré pour le LLM ---\n")
    schema_prompt = get_database_schema(con)
    print(schema_prompt)
