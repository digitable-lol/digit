"""Contract for offline, immutable mascots distributed with Digit."""

from __future__ import annotations

from agent.pet import store
from agent.pet.constants import FRAME_H, FRAME_W


def test_digitmorf_is_explicitly_selectable_without_polluting_empty_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / ".digit"))

    assert store.installed_pets() == []

    pet = store.load_pet("digitmorf")
    assert pet is not None
    assert pet.exists
    assert pet.bundled is True
    assert pet.render_kind == "digitmorf-3d"
    assert store.resolve_active_pet("digitmorf") == pet
    assert store.install_pet("digitmorf") == pet
    assert store.remove_pet("digitmorf") is False


def test_digitmorf_fallback_uses_the_current_eight_by_nine_pet_contract():
    from PIL import Image

    pet = store.load_pet("digitmorf")
    assert pet is not None
    with Image.open(pet.spritesheet) as sheet:
        assert sheet.size == (FRAME_W * 8, FRAME_H * 9)
