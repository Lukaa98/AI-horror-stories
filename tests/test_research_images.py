import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars" / "automation"))

from research_request import (  # noqa: E402
    _auction_provenance_matches_entry,
    _finalize_image_review,
    _generation_commons_terms,
    _normalized_image_category,
    review_and_rename_entry_images,
    valid_images,
)
from cars_and_bids import (  # noqa: E402
    augment_narration_with_current_value,
    format_current_value,
    infer_search_params,
    parse_year_range,
    round_current_value,
)


def test_valid_images_keeps_supported_decodable_files_and_removes_corrupt_ones(tmp_path):
    for filename in ("front.jpg", "rear.png", "interior.webp"):
        Image.new("RGB", (40, 30), (30, 60, 90)).save(tmp_path / filename)
    (tmp_path / "broken.jpg").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("not an image")

    paths = valid_images(tmp_path)

    assert [path.name for path in paths] == ["front.jpg", "interior.webp", "rear.png"]
    assert not (tmp_path / "broken.jpg").exists()


def test_infer_search_params_uses_make_and_model_from_search_hint():
    assert infer_search_params("Audi R8 V10 Plus") == {"make": "audi", "model": "r8"}
    assert infer_search_params("Chevrolet Corvette C5 Z06") == {"make": "chevrolet", "model": "corvette"}


def test_infer_search_params_keeps_toyota_gr_sub_brand_specific():
    """"GR" alone matches Toyota's whole GR sub-brand (GR86, GR Corolla, GR
    Supra), so a bare "gr" model used to search all of them instead of the
    one actually requested -- but this must stay a one-word model for every
    other make/model, since generation/provenance matching elsewhere
    depends on that."""
    assert infer_search_params("Toyota GR Supra 3.0") == {"make": "toyota", "model": "gr supra"}
    assert infer_search_params("Toyota GR Corolla Circuit Edition") == {"make": "toyota", "model": "gr corolla"}
    assert infer_search_params("Toyota GR86 Premium") == {"make": "toyota", "model": "gr86"}


def test_parse_year_range_handles_ranges_and_single_years():
    assert parse_year_range("2007-2012") == (2007, 2012)
    assert parse_year_range("2014") == (2014, 2014)


def test_round_current_value_formats_examples_like_user_requested():
    assert round_current_value(109000) == 110000
    assert format_current_value(109000) == "$110K"
    assert round_current_value(7800) == 8000
    assert format_current_value(7800) == "$8K"


def test_augment_narration_with_current_value_adds_cars_and_bids_context():
    entry = {
        "one_line_fact": "Debuting in 2007 at $109,000 with 414 horsepower, the R8 V8 4.2 Coupe introduced Audi's supercar prowess.",
        "current_value_display": "$110K",
    }
    text = augment_narration_with_current_value(entry)
    assert "Today, clean examples trade around" in text
    assert "$110K" in text


def test_normalized_image_category_maps_loose_vision_labels():
    assert _normalized_image_category("engine") == "engine_bay"
    assert _normalized_image_category("three-quarter") == "exterior_full"
    assert _normalized_image_category("unexpected") == "other_detail"


def test_generation_gallery_provenance_accepts_related_trim_but_rejects_wrong_year():
    exact = {
        "name": "Second-Gen R8 V10 Plus",
        "search_hint": "Audi R8 V10 Plus Type 4S",
        "years": "2015-2018",
        "image_source": {
            "provider": "cars_and_bids",
            "auction_title": "2017 Audi R8 V10 Plus",
        },
    }
    related_trim = {
        **exact,
        "image_source": {
            "provider": "cars_and_bids",
            "auction_title": "2018 Audi R8 V10 Coupe RWS",
        },
    }
    wrong_generation = {
        **exact,
        "image_source": {
            "provider": "cars_and_bids",
            "auction_title": "2012 Audi R8 V10 Coupe",
        },
    }

    assert _auction_provenance_matches_entry(exact) is True
    assert _auction_provenance_matches_entry(related_trim) is True
    assert _auction_provenance_matches_entry(wrong_generation) is False


def test_trusted_gallery_does_not_require_trim_to_be_visible(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    car_dir = images_dir / "r8-plus"
    car_dir.mkdir(parents=True)
    Image.new("RGB", (80, 60), (20, 40, 70)).save(car_dir / "interior-01.jpg")
    entry = {
        "name": "R8 V10 Plus",
        "images": ["images/r8-plus/interior-01.jpg"],
    }
    monkeypatch.setattr(
        "research_request._review_image_with_ai",
        lambda path, candidate, model, **kwargs: {
            "category": "interior",
            "view_description": "dashboard and seats",
            "confidence": 0.6,
            "is_expected_vehicle": False,
            "exact_variant_visible": False,
            "has_visible_contradiction": False,
            "image_quality_usable": True,
            "usable": False,
            "rejection_reason": "trim badge is not visible",
            "provider": "openai",
        },
    )

    review_and_rename_entry_images(
        entry,
        images_dir,
        require_ai=True,
        trusted_variant_provenance=True,
    )

    assert entry["images"] == ["images/r8-plus/interior-01.jpg"]
    assert entry["image_reviews"][0]["usable"] is True


def test_commons_image_needs_model_match_but_not_exact_trim_badge():
    review = {
        "category": "rear",
        "confidence": 0.85,
        "is_expected_vehicle": True,
        "exact_variant_visible": False,
        "has_visible_contradiction": False,
        "image_quality_usable": True,
        "usable": False,
    }

    _finalize_image_review(review, trusted_variant_provenance=False)

    assert review["category"] == "exterior_rear"
    assert review["usable"] is True


def test_porsche_gallery_provenance_matches_generation_without_exact_trim():
    entry = {
        "name": "Boxster 987 S",
        "search_hint": "Porsche Boxster 987 S",
        "years": "2005-2012",
        "image_source": {
            "provider": "cars_and_bids",
            "auction_title": "2012 Porsche Boxster Spyder",
        },
    }

    assert _auction_provenance_matches_entry(entry) is True


def test_commons_terms_put_generation_before_trim():
    terms = _generation_commons_terms({
        "search_hint": "Porsche Boxster 987 S",
        "chassis_code": "987",
        "commons_search_terms": ["Porsche Boxster 987 S"],
    })

    assert terms[:2] == ["porsche boxster 987", "porsche boxster"]


def test_review_renames_from_visual_category_and_rejects_mismatches(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    car_dir = images_dir / "first-gen-r8-v10"
    car_dir.mkdir(parents=True)
    Image.new("RGB", (40, 30), (30, 60, 90)).save(car_dir / "engine-05.jpg")
    Image.new("RGB", (40, 30), (90, 20, 20)).save(car_dir / "rear-01.jpg")
    entry = {
        "name": "First-Gen R8 V10",
        "years": "2009-2015",
        "images": [
            "images/first-gen-r8-v10/engine-05.jpg",
            "images/first-gen-r8-v10/rear-01.jpg",
        ],
    }
    reviews = iter([
        {
            "category": "exterior_full",
            "view_description": "front-left three-quarter exterior",
            "confidence": 0.98,
            "is_expected_vehicle": True,
            "exact_variant_visible": True,
            "usable": True,
            "rejection_reason": None,
            "provider": "openai",
        },
        {
            "category": "exterior_rear",
            "view_description": "different car",
            "confidence": 0.99,
            "is_expected_vehicle": False,
            "exact_variant_visible": False,
            "usable": False,
            "rejection_reason": "wrong generation",
            "provider": "openai",
        },
    ])
    monkeypatch.setattr(
        "research_request._review_image_with_ai",
        lambda path, candidate, model, **kwargs: next(reviews),
    )

    review_and_rename_entry_images(entry, images_dir, require_ai=True)

    assert entry["images"] == ["images/first-gen-r8-v10/exterior_full-01.jpg"]
    assert (car_dir / "exterior_full-01.jpg").exists()
    assert not (car_dir / "engine-05.jpg").exists()
    assert len(entry["image_reviews"]) == 2
    assert entry["image_reviews"][1]["usable"] is False


def test_review_rejects_near_duplicate_used_by_another_entry(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    first_dir = images_dir / "r8-v8"
    second_dir = images_dir / "r8-v10"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    source = Image.new("RGB", (80, 60), (40, 70, 100))
    source.save(first_dir / "detail-03.jpg")
    source.resize((120, 90)).save(second_dir / "highlight-03.jpg")
    seen_images = []
    monkeypatch.setattr(
        "research_request._review_image_with_ai",
        lambda path, candidate, model, **kwargs: {
            "category": "exterior_full",
            "view_description": "side exterior",
            "confidence": 0.95,
            "is_expected_vehicle": True,
            "exact_variant_visible": True,
            "usable": True,
            "rejection_reason": None,
            "provider": "openai",
        },
    )
    first = {"name": "R8 V8", "images": ["images/r8-v8/detail-03.jpg"]}
    second = {"name": "R8 V10", "images": ["images/r8-v10/highlight-03.jpg"]}

    review_and_rename_entry_images(first, images_dir, require_ai=True, seen_images=seen_images)
    review_and_rename_entry_images(second, images_dir, require_ai=True, seen_images=seen_images)

    assert first["images"] == ["images/r8-v8/exterior_full-01.jpg"]
    assert second["images"] == []
    duplicate_review = second["image_reviews"][0]
    assert duplicate_review["rejection_reason"] == "duplicate_or_near_duplicate"
    assert duplicate_review["duplicate_of_entry"] == "R8 V8"
