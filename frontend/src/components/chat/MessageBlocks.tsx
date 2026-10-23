import { ExternalLink, MapPin } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { openExternal } from '../../lib/external';
import { findMedia, mediaUrl, safeSource } from '../../lib/cloud';
import { followFeed } from '../../state/chatCore';
import { openImage } from '../../state/viewers';

/* Блоки сообщений формата app/1: карусель товаров, места на карте, изображение с подписями. */
interface ProductItem { title?: string; price?: string; url?: string; image_media_id?: string }
interface Place { name?: string; lat?: number; lon?: number; url?: string; image_media_id?: string }
interface Annotation { x: number; y: number; text?: string; detail?: string }
export type Block =
  | { type: 'product_carousel'; title?: string; items?: ProductItem[] }
  | { type: 'place_map'; title?: string; places?: Place[] }
  | { type: 'annotated_image'; title?: string; image_media_id?: string; annotations?: Annotation[] }
  | { type: string; title?: string };

const open = (url: string | undefined) => {
  const safe = safeSource({ url });
  if (!safe) return;
  if (window.koddiDesktop) window.location.href = `koddi://open-url?url=${encodeURIComponent(safe.href)}`;
  else window.open(safe.href, '_blank', 'noopener,noreferrer');
};

function Picture({ id, alt }: { id?: string; alt: string }) {
  const media = findMedia(id);
  const src = mediaUrl(media);
  if (!src) return <span className="block-picture is-empty" aria-hidden="true" />;
  return (
    <button
      type="button"
      className="block-picture"
      aria-label={`Посмотреть: ${alt}`}
      onClick={(event) => openImage({ id: media?.file_id, file_id: media?.file_id, name: alt, kind: 'image', media_type: media?.media_type }, src, event.currentTarget.querySelector('img'))}
    >
      <img alt={alt} loading="lazy" decoding="async" draggable={false} src={src} onLoad={followFeed} />
    </button>
  );
}

function Products({ block }: { block: Extract<Block, { type: 'product_carousel' }> }) {
  return (
    <section className="msg-block product-carousel" aria-label={block.title || 'Подборка'}>
      {block.title && <h3>{block.title}</h3>}
      <div className="product-row">
        {(block.items ?? []).map((item, index) => (
          <article key={index} className="product-card">
            <Picture id={item.image_media_id} alt={item.title || 'Товар'} />
            <div className="product-info">
              <b>{item.title}</b>
              {item.price && <span className="product-price">{item.price}</span>}
              {item.url && safeSource({ url: item.url }) && (
                <button type="button" className="product-link" onClick={() => open(item.url)}>Открыть <ExternalLink aria-hidden="true" /></button>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

/** Карта мест: Leaflet грузится при первой карте, плитки приходят через локальный прокси приложения. */
function PlaceMap({ places }: { places: Place[] }) {
  const box = useRef<HTMLDivElement>(null);
  const points = places.filter((place) => Number.isFinite(place.lat) && Number.isFinite(place.lon));
  const signature = JSON.stringify(points.map((place) => [place.lat, place.lon, place.name]));
  useEffect(() => {
    const node = box.current;
    if (!node || !points.length) return undefined;
    let cancelled = false;
    let map: import('leaflet').Map | null = null;
    void (async () => {
      const [module] = await Promise.all([import('leaflet'), import('leaflet/dist/leaflet.css')]);
      if (cancelled) return;
      const L = module.default;
      map = L.map(node, { scrollWheelZoom: false, zoomControl: true, attributionControl: false });
      L.tileLayer('/api/dokky/tile/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
      for (const place of points) {
        L.circleMarker([place.lat as number, place.lon as number], { radius: 8, color: '#ffffff', weight: 2, fillColor: '#438dff', fillOpacity: 1 })
          .bindTooltip(place.name || 'Место')
          .addTo(map);
      }
      const first = points[0] as Place;
      if (points.length === 1) map.setView([first.lat as number, first.lon as number], 15);
      else map.fitBounds(L.latLngBounds(points.map((place) => [place.lat as number, place.lon as number] as [number, number])), { padding: [32, 32] });
      requestAnimationFrame(() => map?.invalidateSize());
    })();
    return () => {
      cancelled = true;
      map?.remove();
    };
    // карта строится заново только при изменении самих точек
  }, [signature]);
  if (!points.length) return null;
  return (
    <div className="place-map" role="group" aria-label="Карта">
      <div ref={box} className="place-map-canvas" />
      <button type="button" className="place-map-credit" onClick={() => openExternal('https://www.openstreetmap.org/copyright')}>© OpenStreetMap</button>
    </div>
  );
}

function Places({ block }: { block: Extract<Block, { type: 'place_map' }> }) {
  return (
    <section className="msg-block place-list" aria-label={block.title || 'Места'}>
      {block.title && <h3>{block.title}</h3>}
      <PlaceMap places={block.places ?? []} />
      <div className="place-grid">
        {(block.places ?? []).map((place, index) => {
          const map = Number.isFinite(place.lat) && Number.isFinite(place.lon) ? `https://www.openstreetmap.org/?mlat=${place.lat}&mlon=${place.lon}#map=16/${place.lat}/${place.lon}` : undefined;
          return (
            <article key={index} className="place-card">
              <Picture id={place.image_media_id} alt={place.name || 'Место'} />
              <div className="place-info">
                <b><MapPin aria-hidden="true" />{place.name}</b>
                <span className="place-actions">
                  {map && <button type="button" onClick={() => open(map)}>На карте</button>}
                  {place.url && safeSource({ url: place.url }) && <button type="button" onClick={() => open(place.url)}>Сайт</button>}
                </span>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function Annotated({ block }: { block: Extract<Block, { type: 'annotated_image' }> }) {
  const media = findMedia(block.image_media_id);
  const src = mediaUrl(media);
  const notes = block.annotations ?? [];
  if (!src) return null;
  return (
    <section className="msg-block annotated" aria-label={block.title || 'Изображение с подписями'}>
      {block.title && <h3>{block.title}</h3>}
      <div className="annotated-frame">
        <img alt={block.title || media?.alt || 'Изображение'} loading="lazy" decoding="async" draggable={false} src={src} onLoad={followFeed} />
        {notes.map((note, index) => (
          <span key={index} className="annotation-pin" style={{ left: `${Math.min(1, Math.max(0, note.x)) * 100}%`, top: `${Math.min(1, Math.max(0, note.y)) * 100}%` }} title={note.text}>{index + 1}</span>
        ))}
      </div>
      {notes.length > 0 && (
        <ol className="annotation-list">
          {notes.map((note, index) => <li key={index}><b>{note.text}</b>{note.detail && <span> {note.detail}</span>}</li>)}
        </ol>
      )}
    </section>
  );
}

export function MessageBlocks({ blocks }: { blocks: Block[] | undefined }) {
  if (!blocks?.length) return null;
  return (
    <div className="message-blocks">
      {blocks.map((block, index) => {
        if (block.type === 'product_carousel') return <Products key={index} block={block as Extract<Block, { type: 'product_carousel' }>} />;
        if (block.type === 'place_map') return <Places key={index} block={block as Extract<Block, { type: 'place_map' }>} />;
        if (block.type === 'annotated_image') return <Annotated key={index} block={block as Extract<Block, { type: 'annotated_image' }>} />;
        return null;
      })}
    </div>
  );
}
