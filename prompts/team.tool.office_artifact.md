## office_artifact

Create, read and edit Writer documents in the active project's Files. Use ODT by default and DOCX when Word compatibility is needed. Use `text_editor` for plain text or Markdown.

- `create`: Supply `title`, `content` and optionally a project-relative `path`. The filename extension must match `format`. Choose a new filename for each new document.
- `read`: Use `path` or the `file_id` returned by this tool. The result includes document text and its current `revision`.
- `edit`: Pass the latest `document.revision` from `create` or `read`. Use `set_text`, `append_text` or `prepend_text` with `content`. Use `replace_text` with `find` and `replace`, or `delete_text` with `find`. Read again when a revision conflict occurs.
- `open` and `inspect`: Return project file metadata. The user can view the saved file in the project's Files and open it in Desktop.
- `status`: Return the formats and actions available in a project chat.

Input schema for tool_args:
{
  "type": "object",
  "properties": {
    "action": {"type": "string", "enum": ["create", "open", "read", "edit", "inspect", "status"]},
    "title": {"type": "string"},
    "format": {"type": "string", "enum": ["odt", "docx"]},
    "content": {"type": "string"},
    "path": {"type": "string"},
    "file_id": {"type": "string"},
    "revision": {"type": "string"},
    "operation": {"type": "string", "enum": ["set_text", "append_text", "prepend_text", "replace_text", "delete_text"]},
    "find": {"type": "string"},
    "replace": {"type": "string"},
    "count": {"type": "integer"},
    "max_chars": {"type": "integer"}
  },
  "required": ["action"]
}
