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

# ─────────────────── 🔐 Load Dropbox token from Streamlit Secrets ─────────────────── #
DROPBOX_TOKEN = st.secrets.get("DROPBOX_TOKEN")

if not DROPBOX_TOKEN:
    st.error(
        "❌ Dropbox API token not found.\n"
        "Please add `DROPBOX_TOKEN = \"sl.your_token_here\"` to your Streamlit secrets."
    )
    st.stop()

# ─────────────────── ⚙️ Optional app settings ─────────────────── #
def load_settings(json_file="settings.json"):
    if not os.path.exists(json_file):
        return {}
    with open(json_file, "r", encoding="utf-8") as f:
        return json.load(f)

settings     = load_settings()
DROPBOX_ROOT = settings.get("dropbox_root", "")

# ─────────────────── ☁️ Dropbox helper functions ─────────────────── #
def dbx_client():
    return dropbox.Dropbox(DROPBOX_TOKEN)

def list_dropbox_excels(root_folder=""):
    dbx, results, queue = dbx_client(), [], [root_folder]
    try:
        while queue:
            current = queue.pop(0)
            for entry in dbx.files_list_folder(current).entries:
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

def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-_.() ]", "_", str(name)).replace(" ", "_")

def upload_file_to_dropbox(local_path: str, dropbox_dest: str):
    try:
        dbx = dbx_client()
        with open(local_path, "rb") as f:
            dbx.files_upload(f.read(), dropbox_dest, mode=dropbox.files.WriteMode.overwrite)
        st.success(f"✅ Uploaded to `{dropbox_dest}`")
    except dropbox.exceptions.ApiError as e:
        st.error(f"❌ Dropbox API error: {e}")
    except FileNotFoundError:
        st.error(f"❌ Local file not found: {local_path}")
    except Exception as e:
        st.error(f"❌ Unexpected upload error: {e}")

# ─────────────────── 🔁 Session State ─────────────────── #
st.session_state.setdefault("images_by_model", {})
st.session_state.setdefault("capture_key", 0)

# ─────────────────── 🌐 Streamlit UI ─────────────────── #
st.set_page_config(page_title="📸 Dropbox Upload Tool", layout="centered")
st.title("📸 Capture Product Images & Upload to Dropbox")

excel_files   = list_dropbox_excels(DROPBOX_ROOT)
selected_excel = st.selectbox("📂 Select Excel file:", excel_files)

if selected_excel:
    sheets, model_opts, model_map = read_excel_from_dropbox(selected_excel), [], {}
    for sheet, df in sheets.items():
        if "Model" in df.columns:
            for idx, row in df.iterrows():
                model  = str(row["Model"])
                info   = " | ".join(f"{k}: {v}" for k, v in row.items() if k != "Model")
                dlabel = f"{model} ({info})" if info else model
                model_opts.append(dlabel)
                model_map[dlabel] = {"model": model, "row": idx + 2}

    chosen_label = st.selectbox("🔍 Select product model:", sorted(model_opts))
    chosen = model_map[chosen_label]
    chosen_model, chosen_row = chosen["model"], chosen["row"]

    st.markdown("### 📷 Capture new image")
    pic = st.camera_input("📸 Camera", key=f"cam_{st.session_state.capture_key}")

    if pic:
        fname = sanitize_filename(f"{chosen_model}_{len(st.session_state.images_by_model.get(chosen_label, [])) + 1}.jpg")
        st.session_state.images_by_model.setdefault(chosen_label, []).append(
            {"filename": fname, "data": pic.getvalue(), "row": chosen_row, "model": chosen_model}
        )
        st.session_state.capture_key += 1
        st.experimental_rerun()

    if st.session_state.images_by_model:
        st.markdown("### 🖼️ Image Preview")
        for label, imgs in st.session_state.images_by_model.items():
            if imgs:
                st.markdown(f"**📁 {label}**")
                for i, img in enumerate(imgs):
                    col_img, col_btn = st.columns([5, 1])
                    with col_img:
                        st.image(Image.open(io.BytesIO(img["data"])), caption=img["filename"], use_column_width=True)
                    with col_btn:
                        if st.button("🗑️", key=f"del_{label}_{i}"):
                            st.session_state.images_by_model[label].pop(i)
                            st.experimental_rerun()

    if any(st.session_state.images_by_model.values()):
        if st.button("📤 Upload all"):
            try:
                base   = sanitize_filename(PurePosixPath(selected_excel).stem)
                parent = PurePosixPath(selected_excel).parent.as_posix().strip("/")
                root   = f"/{parent}/{base}".replace("//", "/")
                st.info("🔄 Uploading...")

                for label, imgs in st.session_state.images_by_model.items():
                    for img in imgs:
                        sub    = sanitize_filename(str(img["row"]))
                        dbpath = f"{root}/{sub}/{sanitize_filename(img['filename'])}".replace("//", "/")
                        st.write(f"📤 Uploading to `{dbpath}`")

                        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                            tmp.write(img["data"])
                            upload_file_to_dropbox(tmp.name, dbpath)

                st.session_state.images_by_model.clear()
                st.success("🎉 All images uploaded successfully.")
                st.balloons()
            except Exception as e:
                st.error(f"❌ Upload error: {e}")
