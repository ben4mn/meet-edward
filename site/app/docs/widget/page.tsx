import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata = {
  title: "Widget — Edward Docs",
};

export default function WidgetPage() {
  return (
    <DocsContent>
      <h1>Widget</h1>
      <p className="subtitle">
        Put Edward on your iOS home screen with a Scriptable widget.
      </p>

      <h2>Overview</h2>
      <p>
        Edward can render a live widget on your iPhone or iPad home screen using
        the{" "}
        <a href="https://scriptable.app" target="_blank" rel="noopener noreferrer">
          Scriptable
        </a>{" "}
        app. The widget fetches data from Edward&apos;s API and displays it
        natively — upcoming events, memory stats, custom dashboards, or
        anything Edward decides to show you.
      </p>

      <h2>How It Works</h2>
      <pre><code>{`Edward (LLM tools)
       ↓
  PostgreSQL (widget_state)
       ↓
  GET /api/widget?token=<token>
       ↓
  Scriptable app (iOS)
       ↓
  Home screen widget`}</code></pre>
      <p>
        Edward updates the widget state via LLM tools. The Scriptable app
        periodically fetches the latest state from the public API endpoint
        (authenticated by token, no login required) and renders it.
      </p>

      <h2>Setup</h2>
      <ol>
        <li>
          Install{" "}
          <a href="https://apps.apple.com/app/scriptable/id1405459188" target="_blank" rel="noopener noreferrer">
            Scriptable
          </a>{" "}
          from the App Store
        </li>
        <li>
          Get your widget token from Edward&apos;s settings page or via{" "}
          <code>GET /api/widget/token</code>
        </li>
        <li>
          Create a new script in Scriptable and paste the widget code (available
          in settings)
        </li>
        <li>
          Update the <code>TOKEN</code> and <code>BASE_URL</code> variables in
          the script to point to your Edward instance
        </li>
        <li>
          Add a Scriptable widget to your home screen and select the script
        </li>
      </ol>

      <h2>Structured Data Mode</h2>
      <p>
        By default, the widget uses structured data — Edward sets a title,
        subtitle, theme, and an array of sections. Available section types:
      </p>
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>header</code></td>
            <td>Bold section header text</td>
          </tr>
          <tr>
            <td><code>text</code></td>
            <td>Plain text content</td>
          </tr>
          <tr>
            <td><code>list</code></td>
            <td>Bulleted list of items</td>
          </tr>
          <tr>
            <td><code>stat</code></td>
            <td>Single key-value statistic</td>
          </tr>
          <tr>
            <td><code>stats_row</code></td>
            <td>Horizontal row of multiple stats</td>
          </tr>
          <tr>
            <td><code>progress</code></td>
            <td>Progress bar with label and percentage</td>
          </tr>
          <tr>
            <td><code>countdown</code></td>
            <td>Countdown to a target date</td>
          </tr>
          <tr>
            <td><code>divider</code></td>
            <td>Visual separator line</td>
          </tr>
          <tr>
            <td><code>spacer</code></td>
            <td>Empty vertical space</td>
          </tr>
        </tbody>
      </table>
      <p>
        Themes control the color scheme. Edward can set the theme dynamically
        based on time of day or content. Each section can include an optional
        SF Symbol icon.
      </p>

      <h2>Raw JavaScript Mode</h2>
      <p>
        For full control, Edward can store raw Scriptable JavaScript via the{" "}
        <code>update_widget_code</code> tool. The script must create a{" "}
        <code>ListWidget</code> and call <code>Script.setWidget()</code>. When
        custom code is set, the structured data mode is bypassed entirely.
      </p>
      <p>
        Use <code>clear_widget_code</code> to revert back to structured data
        mode.
      </p>

      <h2>Auto-Generated Default</h2>
      <p>
        When no custom widget state has been set, Edward auto-generates content
        including:
      </p>
      <ul>
        <li>A time-of-day greeting</li>
        <li>Upcoming scheduled events</li>
        <li>Memory statistics</li>
      </ul>

      <h2>Widget Chat</h2>
      <p>
        The widget can send quick messages to Edward via{" "}
        <code>POST /api/widget/chat</code>. This is a lightweight endpoint that
        allows simple interactions without opening the full web UI.
      </p>

      <h2>LLM Tools</h2>
      <p>
        Widget tools are always available (not skill-gated):
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
            <td><code>update_widget</code></td>
            <td>Set widget title, subtitle, sections, and theme</td>
          </tr>
          <tr>
            <td><code>get_widget_state_tool</code></td>
            <td>Read the current widget state</td>
          </tr>
          <tr>
            <td><code>update_widget_code</code></td>
            <td>Store raw Scriptable JavaScript</td>
          </tr>
          <tr>
            <td><code>clear_widget_code</code></td>
            <td>Clear custom script, revert to structured data</td>
          </tr>
        </tbody>
      </table>
    </DocsContent>
  );
}
