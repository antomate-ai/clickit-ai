
import os
import io
import json
import tempfile
import re
from pathlib import PurePosixPath

import streamlit as st
import pandas as pd
import dropbox
from PIL import Image
from dotenv import load_dotenv

# --------------------- 🔐 Load Dropbox Token --------------------- #
def load_dropbox_token():
    try:
        return st.secrets["DROPBOX_TOKEN"]
    except Exception:
        return os.getenv("DROPBOX_TOKEN")

load_dotenv()  # Load .env (local)
DROPBOX_TOKEN = load_dropbox_token()

if not DROPBOX_TOKEN:
    st.error("❌ Dropbox API token not found. Please set 'DROPBOX_TOKEN' in Streamlit Secrets or .env.")
    st.stop()

# --------------------- ⚙️ Load Optional Settings --------------------- #
def load_settings(json_file="settings.json"):
    if not os.path.exists(json_file):
        return {}
    with open(json_file, "r", encoding="utf-8") as f:
        return json.load(f)

settings = load_settings()
DROPBOX_ROOT = settings.get("dropbox_root", "")

# --------------------- ☁️ Dropbox Helper Functions --------------------- #
def dbx_client():
    return dropbox.Dropbox(DROPBOX_TOKEN)

def list_dropbox_excels(root_folder=""):
    dbx = dbx_client()
    results = []
    try:
        queue = [root_folder]
        while queue:
            current = queue.pop(0)
            entries = dbx.files_list_folder(current).entries
            for entry in entries:
                if isinstance(entry, dropbox.files.FolderMetadata):
                    queue.append(entry.path_lower)
                elif isinstance(entry, dropbox.files.FileMetadata) and entry.name.lower().endswith((".xls", ".xlsx")):
                    results.append(entry.path_display)
    except Exception as e:
        st.error(f"Error listing Dropbox files: {e}")
    return sorted(results)

def read_excel_from_dropbox(path):
    try:
        dbx = dbx_client()
        _, res = dbx.files_download(path)
        return pd.read_excel(io.BytesIO(res.content), sheet_name=None)
    except Exception as e:
        st.error(f"❌ Error reading Excel file: {e}")
        return {}

def sanitize_filename(name):
    name = str(name)
    name = re.sub(r"[^\w\-_.() ]", "_", name)
    return name.replace(" ", "_")

def upload_file_to_dropbox(local_path, dropbox_dest):
    try:
        dbx = dbx_client()
        with open(local_path, "rb") as f:
            data = f.read()
        dbx.files_upload(data, dropbox_dest, mode=dropbox.files.WriteMode.overwrite)
        st.success(f"✅ Uploaded to `{dropbox_dest}`")
    except dropbox.exceptions.ApiError as e:
        st.error(f"❌ Dropbox API error: {e}")
    except FileNotFoundError:
        st.error(f"❌ Local file not found: {local_path}")
    except Exception as e:
        st.error(f"❌ Unexpected upload error: {e}")

# --------------------- 🔁 Session State --------------------- #
if "images_by_model" not in st.session_state:
    st.session_state.images_by_model = {}
if "capture_key" not in st.session_state:
    st.session_state.capture_key = 0

# --------------------- 🌐 Streamlit UI --------------------- #
st.set_page_config(page_title="📸 Dropbox Upload Tool", layout="centered")
st.title("📸 Capture Product Images & Upload to Dropbox")

excel_files = list_dropbox_excels(DROPBOX_ROOT)
selected_excel = st.selectbox("📂 Select Excel file:", excel_files)

if selected_excel:
    sheets = read_excel_from_dropbox(selected_excel)
    model_options = []
    model_map = {}

    for sheet_name, df in sheets.items():
        if "Model" in df.columns:
            for i, row in df.iterrows():
                model = str(row["Model"])
                info = " | ".join(f"{k}: {v}" for k, v in row.items() if k != "Model")
                display = f"{model} ({info})" if info else model
                model_options.append(display)
                model_map[display] = {
                    "model": model,
                    "row": i + 2  # Excel row number (with header)
                }

    selected_display = st.selectbox("🔍 Select product model:", sorted(model_options))
    selected = model_map[selected_display]
    selected_model = selected["model"]
    selected_row = selected["row"]

    st.markdown("### 📷 Capture new image")
    image_file = st.camera_input("📸 Camera", key=f"cam_{st.session_state.capture_key}")

    if image_file:
        filename = sanitize_filename(f"{selected_model}_{len(st.session_state.images_by_model.get(selected_display, [])) + 1}.jpg")
        if selected_display not in st.session_state.images_by_model:
            st.session_state.images_by_model[selected_display] = []
        st.session_state.images_by_model[selected_display].append({
            "filename": filename,
            "data": image_file.getvalue(),
            "row": selected_row,
            "model": selected_model
        })
        st.session_state.capture_key += 1
        st.experimental_rerun()

    if st.session_state.images_by_model:
        st.markdown("### 🖼️ Image Preview")
        for key, images in st.session_state.images_by_model.items():
            if images:
                st.markdown(f"**📁 {key}**")
                for i, img in enumerate(images):
                    cols = st.columns([5, 1])
                    with cols[0]:
                        st.image(Image.open(io.BytesIO(img["data"])), caption=img["filename"], use_column_width=True)
                    with cols[1]:
                        if st.button("🗑️", key=f"del_{key}_{i}"):
                            st.session_state.images_by_model[key].pop(i)
                            st.experimental_rerun()

    # 📤 Upload to Dropbox
    if any(st.session_state.images_by_model.values()):
        if st.button("📤 Upload all"):
            try:
                excel_base = sanitize_filename(PurePosixPath(selected_excel).stem)
                excel_parent_raw = PurePosixPath(selected_excel).parent
                excel_parent = excel_parent_raw.as_posix()

                if excel_parent in [".", "", "/"]:
                    excel_parent = ""

                root_upload_folder = f"/{excel_parent}/{excel_base}".replace("//", "/")
                st.info("🔄 Uploading...")

                for key, images in st.session_state.images_by_model.items():
                    for img in images:
                        subfolder = sanitize_filename(str(img["row"]))
                        dropbox_path = f"{root_upload_folder}/{subfolder}/{sanitize_filename(img['filename'])}".replace("//", "/")
                        st.write(f"📤 Uploading to: `{dropbox_path}`")

                        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                            tmp.write(img["data"])
                            tmp_path = tmp.name

                        upload_file_to_dropbox(tmp_path, dropbox_path)

                st.session_state.images_by_model.clear()
                st.success("🎉 All images uploaded successfully.")
                st.balloons()
            except Exception as e:
                st.error(f"❌ Upload error: {e}")
