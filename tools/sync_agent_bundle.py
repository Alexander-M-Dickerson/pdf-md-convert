"""Mirror the canonical packaged skill into the repository discovery folder."""
from pathlib import Path
import shutil
root = Path(__file__).resolve().parents[1]
source = root / "src/pdf_md_convert/resources/agent_bundle/.agents"
shutil.copytree(source, root / ".agents", dirs_exist_ok=True)
print("Repository skill synchronized")
