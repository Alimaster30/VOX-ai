import argostranslate.package
import argostranslate.translate

print("Updating package index...")
argostranslate.package.update_package_index()
available = argostranslate.package.get_available_packages()

pairs = [("ur", "en"), ("en", "ur")]
for from_code, to_code in pairs:
    pkg = next((p for p in available if p.from_code == from_code and p.to_code == to_code), None)
    if pkg:
        print(f"Downloading {from_code} → {to_code}...")
        argostranslate.package.install_from_path(pkg.download())
        print(f"[OK] {from_code} → {to_code} installed")
    else:
        print(f"[ERROR] Package {from_code} → {to_code} not found")

print("\n✅ Language packages ready.")
