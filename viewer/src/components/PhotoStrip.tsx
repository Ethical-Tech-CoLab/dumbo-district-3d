import type { CapturedPhoto } from '@d3d/viewer-kernel';

/**
 * Photos the tour captured. Each one is a real framebuffer grab taken at the moment the script's
 * `capture_photo` action fired, which is what makes "the family stopped and took a picture here"
 * an artefact rather than a claim.
 */
export default function PhotoStrip({ photos }: { photos: CapturedPhoto[] }) {
  return (
    <div className="photo-strip">
      <div className="photo-strip-label muted small">
        tour photos · {photos.length}
      </div>
      <div className="photo-strip-scroll">
        {photos.map((photo, index) => (
          <figure key={`${photo.filename}-${index}`}>
            {photo.dataUrl ? (
              <img src={photo.dataUrl} alt={photo.label ?? photo.filename} />
            ) : (
              <div className="photo-pending" />
            )}
            <figcaption className="muted small">{photo.label ?? photo.filename}</figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}
