"""Canned NCBI / Europe PMC / Crossref responses for usiphus_resource_discovery (all content synthetic)."""
from __future__ import annotations

from _common import as_json, install_urlopen, query, url_error

PUBMED = {
    "101": {"title": "Synthetic cohort study of acute urinary retention management", "fulljournalname": "Journal of Synthetic Urology",
            "pubdate": "2025 Mar", "authors": [{"name": "Example A"}, {"name": "Sample B"}],
            "articleids": [{"idtype": "doi", "value": "10.0000/synthetic.101"}], "pubtype": [{"name": "Journal Article"}]},
    "102": {"title": "Unrelated synthetic note on equipment procurement", "fulljournalname": "Synthetic Logistics Review",
            "pubdate": "2024", "authors": [], "articleids": [], "pubtype": []},
}


def handler(method: str, url: str, body):
    q = query(url)
    if "esearch.fcgi" in url:
        ids = ["101", "102"] if "retention" in q["term"].casefold() else []
        return as_json({"esearchresult": {"idlist": ids}})
    if "esummary.fcgi" in url:
        ids = q["id"].split(",")
        return as_json({"result": {"uids": ids, **{i: PUBMED[i] for i in ids}}})
    if "europepmc" in url:
        term = q["query"]
        if "retention" not in term.casefold():
            if "OPEN_ACCESS:Y" in term:
                return url_error("stub-provider-down")      # transport failure for topic 2
            return as_json({"resultList": {"result": []}})
        if "OPEN_ACCESS:Y" in term:
            return as_json({"resultList": {"result": [
                {"pmcid": "PMC9000001", "pmid": "9000001", "doi": "10.0000/synthetic.pmc1",
                 "title": "Open synthetic review: acute urinary retention initial management",
                 "journalTitle": "Synthetic Open Access", "firstPublicationDate": "2025-01-15",
                 "authorList": {"author": [{"fullName": "Open Author"}]},
                 "abstractText": "Initial management of acute urinary retention including catheterisation.",
                 "license": "cc by", "pubType": "review"},
                {"pmid": "101", "title": "Synthetic cohort study of acute urinary retention management",
                 "journalTitle": "Journal of Synthetic Urology", "firstPublicationDate": "2025-03-01",
                 "abstractText": "", "pubType": "research-article"}]}})
        return as_json({"resultList": {"result": []}})          # Cureus: nothing found
    if "api.crossref.org" in url:
        if "retention" in q["query.bibliographic"].casefold():
            return as_json({"message": {"items": [
                {"DOI": "10.0000/synthetic.cr1", "title": ["Synthetic crossref entry on urinary retention"],
                 "author": [{"given": "Cross", "family": "Ref"}], "published": {"date-parts": [[2023, 6]]},
                 "container-title": ["Synthetic Proceedings"], "type": "journal-article",
                 "URL": "https://doi.org/10.0000/synthetic.cr1"}]}})
        return b"<html>not json</html>"                         # invalid JSON for topic 2
    return RuntimeError(f"unstubbed request: {url}")


def install(ctx) -> None:
    install_urlopen(ctx, handler)
