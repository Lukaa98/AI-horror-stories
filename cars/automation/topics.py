"""Registry of ranking-video topics: which Commons categories to scrape and
which render config module to use. Add a new topic here to make it available
in the "topic" dropdown on the Cars Ranking - Generate GitHub Actions workflow.
"""

TOPICS = {
    "miata": {
        "label": "Mazda Miata generations",
        "scrapes": [
            {"category": "Mazda MX-5 (NA)", "topic": "mazda-miata-na"},
            {"category": "Mazda MX-5 (NB)", "topic": "mazda-miata-nb"},
            {"category": "Mazda MX-5 (NC)", "topic": "mazda-miata-nc"},
            {"category": "Mazda MX-5 (ND)", "topic": "mazda-miata-nd"},
        ],
        "render_module": "generate_ranking_short",
    },
    "mustang": {
        "label": "Ford Mustang generations",
        "scrapes": [
            {"category": "Ford Mustang III GT", "topic": "ford-mustang-foxbody"},
            {"category": "Ford Mustang IV", "topic": "ford-mustang-sn95"},
            {"category": "Ford Mustang (2010-2014)", "topic": "ford-mustang-s197"},
            {"category": "Ford Mustang VI", "topic": "ford-mustang-s550"},
        ],
        "render_module": "generate_ranking_short_mustang",
    },
}
