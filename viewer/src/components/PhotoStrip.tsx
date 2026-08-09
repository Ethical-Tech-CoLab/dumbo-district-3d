import type { CapturedPhoto } from '@d3d/viewer-kernel';
import CollapsiblePanel from './CollapsiblePanel';

/**
 * Photos the tour captured. Each is a real framebuffer grab taken at the moment the script's
 * `capture_photo` action fired, which is what makes "the family stopped and took a picture here"
 * an artefact rather than a claim.
 */
export default function PhotoStrip({ photos }: { photos: CapturedPhoto[] }) {
  const summary = (
    <div className="tour-summary">
      <span className="tour-summary-title">Tour photos</span>
      <span className="muted small">{photos.length}</span>
    </div>
  );

  return (
    <CollapsiblePanel storageKey="photos" className="photo-strip" summary={summary}>
      <div className="photo-strip-scroll">
        {photos.map((photo, index) => (
          <figure key={`${photo.filename}-${index}`}>
            {photo.dataUrl ? (
              <a href={photo.dataUrl} download={photo.filename} title={`Download ${photo.filename}`}>
                <img src={photo.dataUrl} alt={photo.label ?? photo.filename} />
              </a>
            ) : (
              <div className="photo-pending" />
            )}
            <figcaption className="muted small">{photo.label ?? photo.filename}</figcaption>
          </figure>
        ))}
      </div>
    </CollapsiblePanel>
  );
}
