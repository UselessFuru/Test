"""Interactive checks on the user's PC; prints results, saves no report/pages."""
from core import CompanionError, LocalAI, load_settings
from web_tools import ToolError, WebTools, search_searxng


def main():
    settings = load_settings()
    print("Checking local search and page reader...", flush=True)
    try:
        search = search_searxng(settings["searxng_url"], "SearXNG documentation")
        print(f"SearXNG: connected, {len(search['results'])} public results.")
        if not search["results"]:
            print("Search has no results. Check its upstream engines or network before asking for a build.")
            return 1
        url = "https://docs.searxng.org/"
        reader = WebTools(settings["searxng_url"], url)
        result = reader.execute("read_page", {"url": url, "focus": "SearXNG"})
        if "error" in result:
            print("Page reader:", result["error"])
            return 1
        print(f"Trafilatura: read {len(result['text'])} characters from the official documentation.")
        reader.clear()
        ai = LocalAI(settings, lambda message: print(message, flush=True))
        print("Local model:", ai.get_model())
        print("Running a real search -> page read -> answer check. This can take a while on a laptop.", flush=True)
        ai.settings.update(web_mode="always", web_workflow="agent")
        answer = ai.answer("Search the web for the official SearXNG documentation, read it, and explain what SearXNG is in one sentence with a source citation.", [])
        print("\n" + answer["answer"])
        names = [row["tool"] for row in answer.get("tool_trace", [])]
        passed = "search_web" in names and "read_page" in names and answer.get("sources") and answer.get("remember", True)
        print("\nFull tool flow: " + ("PASS" if passed else "NOT COMPLETE - see the message above."))
        return 0 if passed else 1
    except (ToolError, CompanionError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
