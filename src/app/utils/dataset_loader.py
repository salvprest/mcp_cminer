import json
import zipfile
import tempfile
import os
from pathlib import Path
from dataclasses import dataclass

from app.utils.logger import setup_logging

logger = setup_logging("DEBUG", __name__)


@dataclass
class DatasetInfo:
    """Parsed dataset metadata."""
    title: str
    version: str
    last_updated: str
    model_count: int
    repository_url: str | None = None
    homepage_url: str | None = None
    models_dir: Path | None = None
    model_infos: list[dict] | None = None


async def handle_dataset_upload(upload_dir: Path, datasets: dict, filename: str, content: bytes) -> dict:
    """
    Handle dataset ZIP file upload.
    Validates structure, extracts models, and stores metadata.
    """
    # Save ZIP to temp file for validation
    temp_zip = tempfile.NamedTemporaryFile(
        mode='wb', suffix='.zip', delete=False
    )
    temp_zip.write(content)
    temp_zip.close()
    temp_zip_path = Path(temp_zip.name)

    try:
        # Validate ZIP structure
        dataset_info = validate_dataset_zip(temp_zip_path)

        if dataset_info is None:
            return {
                "error": (
                    "Invalid dataset ZIP. Expected structure:\n"
                    "  dataset/dataset.json\n"
                    "  dataset/processed-models/{id}/model.json"
                ),
                "type": "invalid_dataset"
            }
        # Extract models to upload directory
        dataset_name = Path(filename).stem
        datasets_dir = upload_dir / "datasets"
        datasets_dir.mkdir(parents=True, exist_ok=True)

        extracted_models = extract_dataset_models(
            temp_zip_path,
            datasets_dir,
            dataset_name
        )

        # Store dataset metadata
        datasets[dataset_name] = {
            "info": dataset_info,
            "models": extracted_models,
            "extracted_path": datasets_dir / dataset_name,
            "zip_filename": filename,
        }
        logger.info(
            f"Dataset '{dataset_name}' uploaded: "
            f"{len(extracted_models)} models extracted"
        )

        return {
            "type": "dataset",
            "dataset_name": dataset_name,
            "title": dataset_info.title,
            "version": dataset_info.version,
            "model_count": len(extracted_models),
            #"models": [
            #    {
            #        "id": m["model_id"],
            #        "name": m["name"],
            #        "path": str(m["file_path"])
            #    }
            #    for m in extracted_models[:10]  # Preview first 10
            #],
            "message": (
                f"Dataset '{dataset_info.title}' uploaded successfully. "
                f"{len(extracted_models)} models extracted. "
                f"Use cml2graph_load_dataset to load them."
            )
        }
        
    except Exception as e:
        logger.exception(f"Error processing dataset ZIP: {e}")
        return {
            "error": f"Failed to process dataset ZIP: {e}",
            "type": "dataset_error"
        }
    finally:
        # Clean up temp file
        try:
            os.unlink(temp_zip_path)
        except Exception:
            pass

def validate_dataset_zip(zip_path: Path) -> DatasetInfo | None:
    """
    Validate that a ZIP file contains a valid dataset structure.
    Expected structure:
    - dataset/dataset.json (metadata)
    - dataset/processed-models/{model_id}/model.json (graph files)
    
    Returns DatasetInfo if valid, None otherwise.
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            file_list = zf.namelist()
            # Check for dataset.json
            dataset_json_candidates = [
                f for f in file_list 
                if f.endswith('dataset.json')
            ]

            if not dataset_json_candidates:
                logger.warning(f"No dataset.json found in {zip_path}")
                return None
            dataset_json_path = dataset_json_candidates[0]
            # Parse dataset.json
            with zf.open(dataset_json_path) as f:
                dataset_meta = json.load(f)

            # Validate required fields
            required_fields = ['title', 'version', 'lastUpdated', 'modelCount']
            if not all(field in dataset_meta for field in required_fields):
                logger.warning(f"dataset.json missing required fields in {zip_path}")
                return None
            # Find processed-models directory
            base_dir = Path(dataset_json_path).parent
            models_dir = str(base_dir / 'processed-models')
            # Count model.json files
            model_files = [
                f for f in file_list 
                if f.startswith(models_dir) and f.endswith('model.json')
            ]

            if not model_files:
                logger.warning(f"No model.json files found in {zip_path}")
                return None

            logger.info(
                f"Valid dataset found: {dataset_meta['title']} "
                f"with {len(model_files)} models"
            )
            
            return DatasetInfo(
                title=dataset_meta['title'],
                version=dataset_meta['version'],
                last_updated=dataset_meta['lastUpdated'],
                model_count=len(model_files),
                repository_url=dataset_meta.get('repositoryUrl'),
                homepage_url=dataset_meta.get('homepageUrl'),
                model_infos=dataset_meta.get('modelInfos', []),
            )

    except zipfile.BadZipFile:
        logger.warning(f"Invalid ZIP file: {zip_path}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid dataset.json in {zip_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error validating dataset ZIP {zip_path}: {e}")
        return None


def extract_dataset_models(zip_path: Path, target_dir: Path, dataset_name: str) -> list[dict]:
    extracted = []

    with zipfile.ZipFile(zip_path, 'r') as zf:
        file_list = zf.namelist()
        # Find dataset.json to get model metadata
        dataset_json_path = next(
            (f for f in file_list if f.endswith('dataset.json')),
            None
        )
        model_infos_map = {}
        if dataset_json_path:
            with zf.open(dataset_json_path) as f:
                dataset_meta = json.load(f)
                # Build a map of model ID to model info
                for info in dataset_meta.get('modelInfos', []):
                    model_infos_map[info.get('id', '')] = info
        # Find base directory
        base_dir = Path(dataset_json_path).parent if dataset_json_path else Path('dataset')
        models_dir = str(base_dir / 'processed-models')
        # Extract all model.json files
        model_files = [
            f for f in file_list 
            if f.startswith(models_dir) and f.endswith('model.json')
        ]

        for model_file in model_files:
            # Extract model ID from path (e.g., .../processed-models/0132cf79/model.json)
            parts = Path(model_file).parts
            model_id = parts[-2] if len(parts) >= 2 else Path(model_file).stem

            # Create target directory for this model
            model_target_dir = target_dir / dataset_name / model_id
            model_target_dir.mkdir(parents=True, exist_ok=True)
            # Extract model.json
            target_file = model_target_dir / 'model.json'
            with zf.open(model_file) as src, open(target_file, 'wb') as dst:
                dst.write(src.read())

            # Try to find matching model info from dataset.json
            model_info = None
            for info_id, info in model_infos_map.items():
                if info_id.endswith(model_id) or model_id in info_id:
                    model_info = info
                    break
            extracted.append({
                'model_id': model_id,
                'file_path': str(target_file),
                'name': model_info.get('name', model_id) if model_info else model_id,
                'model_info': model_info,
            })
        logger.info(f"Extracted {len(extracted)} models from {zip_path} to {target_dir}")

    return extracted