def test_public_packages_import() -> None:
    import python_hub_contracts
    import python_hub_sdk

    assert python_hub_contracts.__name__ == "python_hub_contracts"
    assert python_hub_sdk.__name__ == "python_hub_sdk"
