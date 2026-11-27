import http.client
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


path = Path(__file__).resolve().parents[1] / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/inspect_shared_project.py"
spec = importlib.util.spec_from_file_location("inspect_shared_project", path)
locator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locator)

CASES = [
    ("settlement-rounding-check", "tokensand-settlement-rounding", "ef9cbdf56ee720c73a8992f3fa1f70d6317106dd", "README.md", True),
    ("invoice-retry-ledger", "tokensand-invoice-retries", "37012ef684ed3cc819bb2c241d82346a61f433eb", "README.md", True),
    ("transaction-cursor-check-1", "tokensand-agent-pack", "10ab9cdf43c368f315695a9066996852f93cb926", "examples/incremental-sync.py", False),
]
url = locator.ORIGIN + "/p/transaction-cursor-check-1"
repo = "https://github.com/giolux1235/tokensand-agent-pack"
pin = repo + "/blob/" + CASES[-1][2] + "/examples/incremental-sync.py"


def page(project=url, *, identity=None, repo_url=None, links=(), duplicate=False, canonical=True):
    record = {"@context":"https://schema.org","@type":"SoftwareSourceCode","url":identity or project,"sameAs":list(links)}
    if repo_url is not None:
        record["codeRepository"] = repo_url
    script = '<script type="application/ld+json">' + json.dumps(record) + '</script>'
    return ('<link rel="canonical" href="' + project + '">' if canonical else '') + script * (2 if duplicate else 1)


class ProjectSources(unittest.TestCase):
    def test_published_examples_preserve_revision_and_distinguish_repository_provenance(self):
        for slug, name, revision, filename, published_repo in CASES:
            with self.subTest(slug=slug):
                project = locator.ORIGIN + "/p/" + slug
                repository = f"https://github.com/giolux1235/{name}"
                source = repository + f"/blob/{revision}/{filename}"
                links = [repository, source] if published_repo else [source]
                code, lines = locator.inspect(project, page(project, repo_url=repository if published_repo else None, links=links))
                self.assertEqual(code, 0)
                self.assertIn("Linked revision: " + revision, lines)
                self.assertIn("Linked file: " + filename, lines)
                label = "Published repository: " if published_repo else "Repository from linked file: "
                self.assertIn(label + repository, lines)
                self.assertIn("Source: " + source, lines)
                self.assertNotIn("passed", "\n".join(lines))

    def test_requires_the_exact_page_and_record(self):
        for html in (page("/p/other"), page(identity=url+"-old"), page(duplicate=True), page(canonical=False),
                     '<link rel="canonical" href="'+url+'"><p>Open demo</p>', page().replace('SoftwareSourceCode','Organization'),
                     page().replace('"sameAs":', '"sameAs":[], "sameAs":'), page().replace('[]', '{')):
            with self.subTest(html=html), self.assertRaises(ValueError):
                locator.inspect(url, html)

    def test_public_link_is_canonicalized_before_fetch(self):
        self.assertEqual(locator.project_url(url+'/?utm_source=teammate#readme'), url)
        for bad in (url.replace('https://','http://'), url.replace('tokensand.com','tokensand.com.other.test'),
                    url.replace('tokensand.com','name@tokensand.com'), url+'/extra', url.replace('/p/','/private/'),
                    url+'%2fmore', url+'\nfoo', url.replace('tokensand.com','tokensand.com:443')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):locator.project_url(bad)

    def test_unpinned_unrelated_or_ambiguous_sources_do_not_select_a_revision(self):
        other="https://github.com/another/repo"
        for declared, links in ((repo,[repo]), (repo,[pin.replace(CASES[-1][2],"main")]), (None,[]),
                            (None,["https://demo.example.org/a"]), (repo,[pin.replace(repo,other)]),
                            (None,[pin,pin+"x"]), (None,[pin,other]), (repo,[pin,other+"/blob/main/file.py"]),
                            (None,[pin.replace("examples/incremental-sync.py","../outside")]),
                            (None,[pin+"?branch=main"]), (None,[pin.replace("github.com","github.com.evil.test")])):
            with self.subTest(links=links):
                code, lines = locator.inspect(url,page(repo_url=declared,links=links))
                self.assertEqual(code,2)
                self.assertFalse(any(line.startswith("Linked revision") for line in lines))

    def test_file_paths_are_data_and_keep_the_published_encoding(self):
        source = pin.rsplit("/", 1)[0] + "/cited%20example.py"
        status, lines = locator.inspect(url, page(links=[source+"#L10-L15",source]))
        self.assertEqual(status, 0)
        self.assertIn("Linked file: examples/cited example.py", lines)
        self.assertIn("Source: "+source, lines)
        for suffix in ("%2Foutside.py", "%2e%2e/outside", "bad%0Aline.py", "%5Cbad.py", "%FE.py"):
            self.assertEqual(locator.inspect(url, page(links=[source.rsplit("/",1)[0] + "/" + suffix]))[0], 2)

    def test_never_visits_source_or_redirect_and_limits_response(self):
        with patch.object(http.client, "HTTPSConnection") as factory:
            conn = factory.return_value
            response = conn.getresponse.return_value
            response.status = 200
            response.headers.get_content_type.return_value = "text/html"
            response.read1.side_effect = [page(links=[pin]).encode(),b""]
            self.assertEqual(locator.fetch_page(url+"?ref=private"),page(links=[pin]))
            self.assertEqual(factory.call_args.args,("tokensand.com",))
            self.assertEqual(conn.request.call_args.args,("GET","/p/transaction-cursor-check-1"))
            self.assertEqual(set(conn.request.call_args.kwargs["headers"]), {"Accept","Accept-Encoding"})
            self.assertEqual(conn.request.call_count,1)
            response.status = 307
            with self.assertRaisesRegex(ValueError,"307"):
                locator.fetch_page(url)
            self.assertEqual(conn.request.call_count,2)
            response.status = 200
            response.read1.side_effect = lambda size: b"x"*size
            with patch.object(locator,"LIMIT",64),self.assertRaisesRegex(ValueError,"limit"):
                locator.fetch_page(url)
            self.assertEqual(conn.close.call_count,3)

    def test_malformed_publisher_fields_and_private_repository_urls_are_not_emitted(self):
        for value in ({"url":repo},repo.replace("https://","http://"),"https://user:pw@github.com/team/repo",
                      repo+"?key=private",repo+"\nnext",repo+'#private'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                locator.inspect(url,page(repo_url=value,links=[pin]))


if __name__ == "__main__":
    unittest.main()
