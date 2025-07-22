@echo off

REM プロジェクトのルートディレクトリに移動する (実際のパスに置き換えてください)
cd /d "C:\python-projects\search-trend-wear-project"

REM 仮想環境をアクティブ化
call venv\Scripts\activate

echo 最新のデータを取得しています (git pull)...
git pull

echo Streamlitアプリケーションを起動します...
streamlit run app.py