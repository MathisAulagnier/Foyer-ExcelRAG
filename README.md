# 📊 Assistant IA Tabulaire (Excel & DuckDB)

Application d'interrogation de données tabulaires volumineuses (~50 000 lignes) combinant un moteur SQL in-memory (**DuckDB**) et un agent de génération de requêtes et de visualisation (**Text-to-SQL + Plotly**).

---

## 🎯 Contexte & Objectif

Ce projet a été conçu pour répondre à la problématique du traitement de fichiers Excel volumineux en IA :
* **Le piège classique :** Le RAG vectoriel naïf (embeddings dans une base vectorielle) échoue sur les agrégations (`SUM`, `AVG`, `GROUP BY`, filtres stricts).
* **L'approche industrielle adoptée :** 
  1. Chargement in-process ultra-rapide dans **DuckDB**.
  2. Extraction dynamique du schéma de la base (DDL + valeurs réelles d'échantillon).
  3. Traduction Langage Naturel $\rightarrow$ SQL avec **boucle d'auto-correction (Self-Correction Loop)**.
  4. Restitution claire et interactive avec **Streamlit**, graphiques **Plotly** à la demande et export CSV.

---

## 🚀 Démarrage local

### 1. Cloner et installer les dépendances
```bash
git clone <votre-repo>
cd ExcelRAG
python -m venv .venv
source .venv/bin/activate  # ou: vf activate foyer
pip install -r requirements.txt
```

### 2. Configuration
Créez un fichier `.env` (optionnel si vous renseignez la clé directement dans l'interface) :
```env
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=nvidia/nemotron-3.5-lightning:free
```

### 3. Lancer l'application
```bash
streamlit run app.py
```

---

## ☁️ Déploiement sur Streamlit Community Cloud

1. Poussez ce dépôt sur GitHub.
2. Rendez-vous sur [share.streamlit.io](https://share.streamlit.io).
3. Sélectionnez le dépôt et pointez vers le fichier `app.py`.
4. *(Optionnel)* Dans les **Settings > Secrets** de votre application Streamlit, vous pouvez ajouter :
   ```toml
   OPENROUTER_API_KEY = "sk-or-v1-..."
   OPENROUTER_MODEL = "nvidia/nemotron-3.5-lightning:free"
   ```
   *Si non renseigné, l'utilisateur est simplement invité à entrer sa propre clé dans la barre latérale.*

---

## 🛠️ Stack Technique
* **Moteur SQL :** DuckDB
* **Traitement & Parsing :** Pandas, Openpyxl
* **Orchestration LLM :** OpenAI SDK (compatible OpenRouter)
* **Interface & Visualisation :** Streamlit, Plotly Express
