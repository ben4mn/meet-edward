import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata = {
  title: "File Storage — Edward Docs",
};

export default function FileStoragePage() {
  return (
    <DocsContent>
      <h1>File Storage</h1>
      <p className="subtitle">
        Persistent file storage with categories, tags, and sandbox integration.
      </p>

      <h2>Overview</h2>
      <p>
        Edward can store files permanently — generated images, code artifacts,
        uploaded documents, processed outputs. Files are organized with
        categories and tags, stored on disk with hex-prefix sharding, and
        tracked in PostgreSQL.
      </p>

      <h2>Storage Architecture</h2>
      <p>
        Files are stored on disk using hex-prefix sharding to avoid overloading
        any single directory:
      </p>
      <pre><code>{`FILE_STORAGE_ROOT/
├── a3/
│   ├── a3f7c2..._report.pdf
│   └── a3e1b0..._chart.png
├── f1/
│   └── f1d4a9..._notes.txt
└── ...`}</code></pre>
      <p>
        The path format is <code>{"{id[0:2]}/{id}_{filename}"}</code>. The
        storage root defaults to <code>./storage</code> and can be configured
        via the <code>FILE_STORAGE_ROOT</code> environment variable.
      </p>

      <h2>File Categories</h2>
      <table>
        <thead>
          <tr>
            <th>Category</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>upload</code></td>
            <td>Files uploaded directly by the user</td>
          </tr>
          <tr>
            <td><code>generated</code></td>
            <td>Files created by Edward (code output, images, etc.)</td>
          </tr>
          <tr>
            <td><code>artifact</code></td>
            <td>Build artifacts or intermediate outputs</td>
          </tr>
          <tr>
            <td><code>processed</code></td>
            <td>Files that have been transformed or analyzed</td>
          </tr>
          <tr>
            <td><code>general</code></td>
            <td>Default category for uncategorized files</td>
          </tr>
        </tbody>
      </table>

      <h2>Sandbox-to-Storage Flow</h2>
      <p>
        When Edward executes code (Python, JavaScript, shell), output files land
        in a per-conversation sandbox directory. These are temporary — cleaned up
        after 24 hours. To keep a file permanently, Edward uses the{" "}
        <code>save_to_storage</code> tool to move it from the sandbox to
        persistent storage.
      </p>
      <pre><code>{`Code execution sandbox (temporary, 24h TTL)
           ↓
  save_to_storage tool
           ↓
  Persistent file storage (permanent)`}</code></pre>

      <h2>Limits</h2>
      <ul>
        <li><strong>Maximum file size</strong>: 50 MB</li>
        <li><strong>Allowed types</strong>: MIME type allowlist (common document, image, audio, video, and code formats)</li>
        <li><strong>Metadata</strong>: Each file tracks filename, MIME type, size, category, tags, source, and timestamps</li>
      </ul>

      <h2>LLM Tools</h2>
      <p>
        File storage tools are always available (not skill-gated):
      </p>
      <table>
        <thead>
          <tr>
            <th>Tool</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>save_to_storage</code></td>
            <td>Move a sandbox file to persistent storage</td>
          </tr>
          <tr>
            <td><code>list_storage_files</code></td>
            <td>List stored files with optional category/tag filters</td>
          </tr>
          <tr>
            <td><code>get_storage_file_url</code></td>
            <td>Get a download URL for a stored file</td>
          </tr>
          <tr>
            <td><code>read_storage_file</code></td>
            <td>Read the contents of a stored text file</td>
          </tr>
          <tr>
            <td><code>tag_storage_file</code></td>
            <td>Update a file&apos;s description, tags, or category</td>
          </tr>
          <tr>
            <td><code>delete_storage_file</code></td>
            <td>Delete a file from storage</td>
          </tr>
        </tbody>
      </table>
    </DocsContent>
  );
}
