import React, { useEffect, useRef, useState } from "react";
import { deleteFile, listFiles, uploadFile } from "../../api/client.js";
import { useConfirm } from "../Confirm.jsx";
import { useToast } from "../Toast.jsx";

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function kindBadge(kind) {
  const map = {
    csv: "📊",
    xlsx: "📈",
    pdf: "📄",
    docx: "📝",
    txt: "📃",
  };
  return map[kind] || "📁";
}

const ACCEPT =
  ".csv,.tsv,.xlsx,.xls,.pdf,.docx,.doc,.txt,.md,.json,.log";

export default function FilesPanel({ userId, threadId, refreshKey, onChanged }) {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef(null);
  const toast = useToast();
  const confirm = useConfirm();

  useEffect(() => {
    let cancel = false;
    async function load() {
      setLoading(true);
      try {
        const data = await listFiles(userId);
        if (!cancel) setFiles(data);
      } catch (_) {
        if (!cancel) setFiles([]);
      } finally {
        if (!cancel) setLoading(false);
      }
    }
    load();
    return () => {
      cancel = true;
    };
  }, [userId, refreshKey]);

  async function handleUpload(fileList) {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    try {
      for (const f of fileList) {
        try {
          const rec = await uploadFile(userId, f, threadId);
          setFiles((xs) => [rec, ...xs.filter((x) => x.id !== rec.id)]);
          toast.success(`Uploaded ${f.name}`);
        } catch (e) {
          toast.error(`Upload failed (${f.name}): ${e.message}`);
        }
      }
      onChanged?.();
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(file) {
    const ok = await confirm({
      title: `Delete ${file.filename}?`,
      body: "The file, its DB row, and all memory nodes/edges referencing it will be permanently removed. This cannot be undone.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await deleteFile(userId, file.id);
      setFiles((xs) => xs.filter((x) => x.id !== file.id));
      const e = res?.graph_edges_removed ?? 0;
      const n = res?.graph_nodes_removed ?? 0;
      toast.success(
        `Deleted ${file.filename}` +
          (e || n ? ` · cleared ${e} edge${e === 1 ? "" : "s"}, ${n} node${n === 1 ? "" : "s"}` : ""),
      );
      onChanged?.();
    } catch (e) {
      toast.error(`Delete failed: ${e.message}`);
    }
  }

  return (
    <div className="files-panel">
      <div
        className={`files-drop${drag ? " files-drop-active" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          handleUpload(e.dataTransfer.files);
        }}
      >
        {uploading ? (
          <span>Uploading…</span>
        ) : (
          <>
            <strong>Drop a file or click to upload</strong>
            <span className="files-drop-hint">
              CSV · XLSX · PDF · DOCX · TXT — max 25 MB
            </span>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          style={{ display: "none" }}
          onChange={(e) => handleUpload(e.target.files)}
        />
      </div>

      <div className="files-list">
        {loading && files.length === 0 && (
          <div className="sidebar-empty">Loading…</div>
        )}
        {!loading && files.length === 0 && (
          <div className="sidebar-empty">
            No files yet. Upload one and ask the bot about it.
          </div>
        )}
        {files.map((f) => (
          <div className="file-row" key={f.id} title={f.summary}>
            <div className="file-row-head">
              <span className="file-kind">{kindBadge(f.kind)}</span>
              <span className="file-name">{f.filename}</span>
              <span className="file-size">{fmtBytes(f.size_bytes)}</span>
              <button
                className="file-delete"
                onClick={() => handleDelete(f)}
                title="Delete file (also wipes its memory nodes)"
                aria-label="Delete file"
              >
                ×
              </button>
            </div>
            {f.summary && <div className="file-summary">{f.summary}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
