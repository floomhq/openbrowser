import {
  Activity,
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  CircleDot,
  Cloud,
  Code2,
  ExternalLink,
  Github,
  Globe2,
  KeyRound,
  Layers3,
  Lock,
  Moon,
  MousePointer2,
  Network,
  ShieldCheck,
  Sparkles,
  Sun,
  Terminal,
  UserRoundCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Separator } from "./components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { cn } from "./lib/utils";

const navItems = [
  { label: "Product", href: "#product" },
  { label: "API", href: "#api" },
  { label: "MCP", href: "#mcp" },
  { label: "Install", href: "#install" },
];

const stats = [
  { label: "Leased browsers", value: "8 / 8", detail: "healthy pool" },
  { label: "Auth mode", value: "same lease", detail: "human and agent share one tab" },
  { label: "Interfaces", value: "API + MCP", detail: "local or remote" },
];

const features = [
  {
    icon: Layers3,
    title: "Session-per-agent leases",
    body: "Agents reserve isolated Chrome sessions, heartbeat during work, then release the browser cleanly.",
  },
  {
    icon: KeyRound,
    title: "Persistent identities",
    body: "Named profiles keep account state for Gmail, LinkedIn, Slack, Microsoft, GitHub, and internal tools.",
  },
  {
    icon: UserRoundCheck,
    title: "Human login handoff",
    body: "Passwords, passkeys, 2FA, QR codes, and approvals happen in a browser portal, not in chat.",
  },
  {
    icon: Globe2,
    title: "Proxy-aware profiles",
    body: "Pin sensitive identities to residential or ISP routes while keeping public QA sessions generic.",
  },
  {
    icon: MousePointer2,
    title: "Browser tools, not a black box",
    body: "Expose screenshots, snapshots, clicks, keyboard input, tabs, uploads, and navigation to the agent.",
  },
  {
    icon: Activity,
    title: "Telemetry and feedback",
    body: "Track leases, failures, browser actions, auth events, and issue reports for later audit.",
  },
];

const integrations = [
  "anthropic",
  "cursor",
  "googlechrome",
  "modelcontextprotocol",
  "github",
  "notion",
  "linear",
  "discord",
  "fastapi",
  "python",
  "docker",
  "cloudflare",
];

function ThemeToggle() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem("openbrowser-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = stored ? stored === "dark" : prefersDark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
  }, []);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("openbrowser-theme", next ? "dark" : "light");
  }

  return (
    <Button variant="outline" size="sm" onClick={toggleTheme} aria-label="Toggle theme">
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      <span className="hidden sm:inline">{dark ? "Light" : "Dark"}</span>
    </Button>
  );
}

function Header() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur-xl">
      <div className="container flex h-16 items-center justify-between gap-4">
        <a href="/" className="flex min-w-0 items-center gap-3" aria-label="OpenBrowser home">
          <img src="/assets/brand/openbrowser-mark.svg" alt="" className="h-8 w-8 dark:invert" />
          <span className="text-base font-semibold tracking-normal">OpenBrowser</span>
        </a>
        <nav className="hidden items-center gap-1 md:flex" aria-label="Primary navigation">
          {navItems.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button asChild size="sm">
            <a href="https://github.com/floomhq/openbrowser">
              <Github className="h-4 w-4" />
              <span className="hidden sm:inline">GitHub</span>
            </a>
          </Button>
        </div>
      </div>
    </header>
  );
}

function BrowserChrome() {
  return (
    <Card className="product-shadow overflow-hidden border-border/80 bg-card/95">
      <div className="flex h-12 items-center gap-3 border-b bg-muted/40 px-4">
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="h-3 w-3 rounded-full bg-red-400" />
          <span className="h-3 w-3 rounded-full bg-amber-400" />
          <span className="h-3 w-3 rounded-full bg-emerald-400" />
        </div>
        <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md border bg-background px-3 py-1.5 text-sm text-muted-foreground">
          <Lock className="h-3.5 w-3.5 text-emerald-600" />
          <span className="truncate">https://browser.floom.dev/openbrowser/v1</span>
        </div>
        <Button variant="outline" size="sm" className="hidden sm:inline-flex">
          Docs
        </Button>
      </div>

      <div className="grid min-h-[520px] bg-background lg:grid-cols-[220px_minmax(0,1fr)_220px]">
        <aside className="border-b bg-muted/20 p-4 lg:border-b-0 lg:border-r">
          <div className="mb-3 text-xs font-semibold uppercase text-muted-foreground">Browser sessions</div>
          <div className="space-y-2">
            {([
              ["work-main", "Active lease", "googlechrome", true],
              ["research-01", "Ready", "modelcontextprotocol", false],
              ["ops-watch", "Idle", "github", false],
            ] as const).map(([name, state, logo, active]) => (
              <div
                key={name}
                className={cn(
                  "flex items-center gap-3 rounded-lg border bg-card p-3",
                  active && "border-emerald-200 bg-emerald-50/60 dark:border-emerald-900 dark:bg-emerald-950/20",
                )}
              >
                <img src={`/assets/logos/${logo}.svg`} alt="" className="h-7 w-7" />
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{name}</div>
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <span className={cn("h-1.5 w-1.5 rounded-full", active ? "bg-emerald-500" : "bg-muted-foreground/50")} />
                    {state}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-5 rounded-lg border bg-card p-4">
            <div className="text-xs font-medium uppercase text-muted-foreground">Capacity</div>
            <div className="mt-2 flex items-end justify-between">
              <span className="text-2xl font-semibold">8</span>
              <span className="text-xs text-muted-foreground">ready slots</span>
            </div>
            <div className="mt-3 h-2 rounded-full bg-muted">
              <div className="h-full w-full rounded-full bg-primary" />
            </div>
          </div>
        </aside>

        <section className="p-4 sm:p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase text-muted-foreground">Live browser session</div>
              <div className="mt-1 flex items-center gap-2 text-sm">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                chrome-depontefede
              </div>
            </div>
            <Badge variant="success">same-lease auth</Badge>
          </div>

          <div className="rounded-lg border bg-card">
            <div className="flex items-center gap-2 border-b p-3">
              <div className="flex gap-1.5" aria-hidden="true">
                <span className="h-2.5 w-2.5 rounded-full bg-muted" />
                <span className="h-2.5 w-2.5 rounded-full bg-muted" />
                <span className="h-2.5 w-2.5 rounded-full bg-muted" />
              </div>
              <div className="ml-2 flex-1 rounded-md bg-muted px-3 py-1.5 text-xs text-muted-foreground">
                techcommunity.microsoft.com
              </div>
            </div>
            <div className="space-y-4 p-5">
              <div className="flex flex-wrap items-center gap-3 border-b pb-4">
                <Badge variant="outline">Tech Community</Badge>
                <span className="text-sm text-muted-foreground">Signed in as federicodeponte</span>
                <Check className="ml-auto h-4 w-4 text-emerald-600" />
              </div>
              <div className="grid gap-4">
                <div className="space-y-3">
                  <div className="h-7 w-3/4 rounded bg-foreground/90" />
                  <div className="h-4 w-full rounded bg-muted" />
                  <div className="h-4 w-5/6 rounded bg-muted" />
                  <div className="grid grid-cols-2 gap-3 pt-3">
                    <div className="rounded-lg border p-3">
                      <div className="h-4 w-20 rounded bg-muted" />
                      <div className="mt-3 h-8 w-16 rounded bg-foreground/90" />
                    </div>
                    <div className="rounded-lg border p-3">
                      <div className="h-4 w-24 rounded bg-muted" />
                      <div className="mt-3 h-8 w-16 rounded bg-foreground/90" />
                    </div>
                  </div>
                </div>
                <div className="rounded-lg border bg-muted/30 p-4">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <Bot className="h-4 w-4" />
                    Agent action
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Continue from the same authenticated tab after human login.
                  </p>
                  <Button size="sm" className="mt-4 w-full">
                    Resume lease
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <aside className="border-t bg-muted/20 p-4 lg:border-l lg:border-t-0">
          <div className="mb-3 text-xs font-semibold uppercase text-muted-foreground">Session state</div>
          <div className="space-y-3">
            {([
              [CircleDot, "Active lease", "28m remaining"],
              [KeyRound, "Profile", "persistent cookies"],
              [Network, "Proxy route", "identity scoped"],
              [ShieldCheck, "Audit", "telemetry clean"],
            ] as const).map(([Icon, title, detail]) => (
              <div key={String(title)} className="flex items-center gap-3 rounded-lg border bg-card p-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-md bg-muted">
                  <Icon className="h-4 w-4" />
                </div>
                <div>
                  <div className="text-sm font-medium">{title}</div>
                  <div className="text-xs text-muted-foreground">{detail}</div>
                </div>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </Card>
  );
}

function CodeTabs() {
  const commands = useMemo(
    () => ({
      api: `curl "$BASE/leases" \\
  -H "authorization: Bearer $OPENBROWSER_API_KEY" \\
  -H "content-type: application/json" \\
  -d '{"owner":"codex","identity_id":"work-main"}'`,
      mcp: `{
  "mcpServers": {
    "openbrowser": {
      "command": "openbrowser-remote-mcp",
      "env": {
        "OPENBROWSER_BASE_URL": "https://browser.example.com/openbrowser/v1",
        "OPENBROWSER_API_KEY": "..."
      }
    }
  }
}`,
      auth: `openbrowser auth https://example.com/login \\
  --identity chrome-depontefede \\
  --owner agent-name`,
    }),
    [],
  );

  return (
    <Tabs defaultValue="api" className="w-full">
      <TabsList className="grid w-full grid-cols-3">
        <TabsTrigger value="api">Remote API</TabsTrigger>
        <TabsTrigger value="mcp">MCP</TabsTrigger>
        <TabsTrigger value="auth">Auth</TabsTrigger>
      </TabsList>
      {Object.entries(commands).map(([key, value]) => (
        <TabsContent key={key} value={key}>
          <pre className="overflow-x-auto rounded-lg border bg-zinc-950 p-4 text-sm text-zinc-50">
            <code>{value}</code>
          </pre>
        </TabsContent>
      ))}
    </Tabs>
  );
}

function Hero() {
  return (
    <section className="relative overflow-hidden border-b">
      <div className="surface-grid pointer-events-none absolute inset-0 opacity-80" />
      <div className="container relative grid gap-12 py-16 lg:grid-cols-[0.82fr_1.18fr] lg:py-24">
        <div className="flex flex-col justify-center">
          <Badge variant="outline" className="mb-5 w-fit gap-2 bg-background/80">
            <Sparkles className="h-3.5 w-3.5" />
            Browser infrastructure for AI agents
          </Badge>
          <h1 className="max-w-3xl text-5xl font-semibold tracking-normal text-foreground sm:text-6xl lg:text-7xl">
            Real browser sessions agents can safely share.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
            OpenBrowser gives Claude, Codex, Cursor, browser-use, and custom workers leased Chrome sessions,
            persistent profiles, human login links, proxy routing, telemetry, and audits.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Button asChild size="lg">
              <a href="https://github.com/floomhq/openbrowser">
                <Github className="h-4 w-4" />
                Open GitHub
              </a>
            </Button>
            <Button asChild variant="outline" size="lg">
              <a href="#install">
                Install
                <ArrowRight className="h-4 w-4" />
              </a>
            </Button>
          </div>
          <div className="mt-8 grid gap-3 sm:grid-cols-3">
            {stats.map((stat) => (
              <div key={stat.label} className="rounded-lg border bg-background/80 p-4 backdrop-blur">
                <div className="text-2xl font-semibold">{stat.value}</div>
                <div className="mt-1 text-xs font-medium uppercase text-muted-foreground">{stat.label}</div>
                <div className="mt-2 text-sm text-muted-foreground">{stat.detail}</div>
              </div>
            ))}
          </div>
        </div>
        <div id="product" className="min-w-0">
          <BrowserChrome />
        </div>
      </div>
    </section>
  );
}

function LogoStrip() {
  return (
    <section className="border-b bg-muted/20">
      <div className="container py-8">
        <div className="mb-5 text-center text-sm font-medium text-muted-foreground">
          Works with the agent stack you already run
        </div>
        <div className="grid grid-cols-3 items-center gap-5 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-12">
          {integrations.map((name) => (
            <div key={name} className="flex h-12 items-center justify-center rounded-lg border bg-card">
              <img src={`/assets/logos/${name}.svg`} alt={`${name} logo`} className="h-5 max-w-24 opacity-80" />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section className="container py-20">
      <div className="mx-auto max-w-2xl text-center">
        <Badge variant="secondary" className="mb-4">
          Architecture
        </Badge>
        <h2 className="text-4xl font-semibold tracking-normal sm:text-5xl">One browser contract for every agent.</h2>
        <p className="mt-4 text-lg leading-8 text-muted-foreground">
          Stop fighting one shared CDP target. Give agents a lease, a profile, a browser tool surface, and an audit trail.
        </p>
      </div>
      <div className="mt-12 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {features.map((feature) => (
          <Card key={feature.title}>
            <CardHeader>
              <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-md border bg-muted">
                <feature.icon className="h-5 w-5" />
              </div>
              <CardTitle className="text-xl">{feature.title}</CardTitle>
              <CardDescription className="text-base leading-7">{feature.body}</CardDescription>
            </CardHeader>
          </Card>
        ))}
      </div>
    </section>
  );
}

function ApiSection() {
  return (
    <section id="api" className="border-y bg-muted/20">
      <div className="container grid gap-10 py-20 lg:grid-cols-[0.85fr_1.15fr]">
        <div className="flex flex-col justify-center">
          <Badge variant="outline" className="mb-4 w-fit">
            Remote API
          </Badge>
          <h2 className="text-4xl font-semibold tracking-normal sm:text-5xl">Call a real browser from any machine.</h2>
          <p className="mt-4 text-lg leading-8 text-muted-foreground">
            Put OpenBrowser behind HTTPS and give agents a bearer token. The same API handles leases,
            tabs, navigation, screenshots, keyboard events, auth handoff, telemetry, and audits.
          </p>
          <div className="mt-8 grid gap-3">
            {[
              "No raw CDP ports for normal agent work",
              "Named profiles for persistent account state",
              "Same-lease login handoff for passwords and passkeys",
            ].map((item) => (
              <div key={item} className="flex items-center gap-3 text-sm">
                <Check className="h-4 w-4 text-emerald-600" />
                {item}
              </div>
            ))}
          </div>
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-xl">
              <Terminal className="h-5 w-5" />
              Developer surface
            </CardTitle>
            <CardDescription>API, MCP, and CLI use the same broker-backed session model.</CardDescription>
          </CardHeader>
          <CardContent>
            <CodeTabs />
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

function FlowSection() {
  const steps = [
    [Bot, "Agent leases", "Reserve an isolated Chrome slot with an optional identity."],
    [Cloud, "Broker opens", "Navigate, snapshot, screenshot, click, type, upload, and inspect tabs."],
    [UserRoundCheck, "Human approves", "Use /auth links for passwords, passkeys, 2FA, and login walls."],
    [Activity, "Audit remains", "Telemetry and feedback show who used which browser, when, and why."],
  ] as const;

  return (
    <section id="mcp" className="container py-20">
      <div className="mb-10 flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <Badge variant="secondary" className="mb-4">
            MCP native
          </Badge>
          <h2 className="max-w-2xl text-4xl font-semibold tracking-normal sm:text-5xl">
            Browser control that stays understandable.
          </h2>
        </div>
        <p className="max-w-xl text-lg leading-8 text-muted-foreground">
          OpenBrowser is not a mystery agent. It is a reliable browser layer that lets the calling model steer the work.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-4">
        {steps.map(([Icon, title, body], index) => (
          <Card key={title} className="relative overflow-hidden">
            <CardHeader>
              <div className="mb-6 flex items-center justify-between">
                <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary text-primary-foreground">
                  <Icon className="h-5 w-5" />
                </div>
                {index < steps.length - 1 && <ChevronRight className="hidden h-5 w-5 text-muted-foreground md:block" />}
              </div>
              <CardTitle className="text-xl">{title}</CardTitle>
              <CardDescription className="text-base leading-7">{body}</CardDescription>
            </CardHeader>
          </Card>
        ))}
      </div>
    </section>
  );
}

function InstallSection() {
  return (
    <section id="install" className="border-y bg-zinc-950 text-zinc-50">
      <div className="container grid gap-10 py-20 lg:grid-cols-[0.9fr_1.1fr]">
        <div>
          <Badge className="mb-4 bg-zinc-800 text-zinc-100">Open source</Badge>
          <h2 className="text-4xl font-semibold tracking-normal sm:text-5xl">Run it on your own browser host.</h2>
          <p className="mt-4 text-lg leading-8 text-zinc-400">
            Install the broker, expose the API through your own HTTPS domain, and connect local or remote agents through
            MCP or HTTP.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Button asChild variant="secondary" size="lg">
              <a href="https://github.com/floomhq/openbrowser">
                <Github className="h-4 w-4" />
                View repository
              </a>
            </Button>
            <Button asChild variant="outline" size="lg" className="border-zinc-700 bg-transparent text-zinc-50 hover:bg-zinc-900">
              <a href="https://github.com/floomhq/openbrowser/blob/main/docs/openbrowser-api.md">
                API docs
                <ExternalLink className="h-4 w-4" />
              </a>
            </Button>
          </div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm text-zinc-400">
            <Code2 className="h-4 w-4" />
            Quick start
          </div>
          <pre className="overflow-x-auto text-sm leading-7 text-zinc-100">
            <code>{`git clone https://github.com/floomhq/openbrowser.git
cd openbrowser
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
openbrowser-broker

OPENBROWSER_API_KEYS="$(openssl rand -base64 48)"
openbrowser docs quickstart`}</code>
          </pre>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="container py-10">
      <Separator className="mb-8" />
      <div className="flex flex-col justify-between gap-4 text-sm text-muted-foreground md:flex-row md:items-center">
        <div className="flex items-center gap-3">
          <img src="/assets/brand/openbrowser-mark.svg" alt="" className="h-6 w-6 dark:invert" />
          <span>OpenBrowser</span>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <a className="hover:text-foreground" href="https://github.com/floomhq/openbrowser">
            GitHub
          </a>
          <a className="hover:text-foreground" href="https://github.com/floomhq/openbrowser/blob/main/docs/openbrowser-api.md">
            API docs
          </a>
          <a className="hover:text-foreground" href="https://github.com/floomhq/openbrowser/blob/main/LICENSE">
            MIT License
          </a>
        </div>
      </div>
    </footer>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main>
        <Hero />
        <LogoStrip />
        <Features />
        <ApiSection />
        <FlowSection />
        <InstallSection />
      </main>
      <Footer />
    </div>
  );
}
