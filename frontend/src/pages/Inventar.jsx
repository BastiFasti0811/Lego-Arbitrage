import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import ListingManager from "../components/ListingManager";
import PhotoFirstModal from "../components/PhotoFirstModal";
import InventoryAddDialog from "../components/inventory/InventoryAddDialog";
import InventoryDetail, { InventorySellDialog } from "../components/inventory/InventoryDetail";
import InventoryFilters from "../components/inventory/InventoryFilters";
import InventoryHeader from "../components/inventory/InventoryHeader";
import InventoryList from "../components/inventory/InventoryList";
import {
  createLocalPhotoEntries,
  emptyAddForm,
  normalizeLocation,
  revokeLocalPhotoEntries,
} from "../components/inventory/inventoryFormat";
import { useToast } from "../components/toastContext";
import { filterInventory, prepareListingWithToast } from "./listingPrep";

// Inventar als Verkaufs-Cockpit: Seite haelt Zustand, Abfragen und Mutationen;
// Darstellung in components/inventory (Kopf, Filter, Liste, Dialoge).

export default function Inventar() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [sellModal, setSellModal] = useState(null);
  const [sellPrice, setSellPrice] = useState("");
  const [sellDate, setSellDate] = useState(new Date().toISOString().split("T")[0]);
  const [sellPlatform, setSellPlatform] = useState("");
  const [addModal, setAddModal] = useState(false);
  const [photoFirstModal, setPhotoFirstModal] = useState(false);
  const [editModal, setEditModal] = useState(null);
  const [editForm, setEditForm] = useState({});
  const [lookupLoading, setLookupLoading] = useState(false);
  const [duplicates, setDuplicates] = useState([]);
  const [addForm, setAddForm] = useState(emptyAddForm());
  const [listingItem, setListingItem] = useState(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [listingFilter, setListingFilter] = useState("");
  const [photoFilter, setPhotoFilter] = useState("");
  const [search, setSearch] = useState("");
  const [preparingId, setPreparingId] = useState(null);
  const [addPhotoEntries, setAddPhotoEntries] = useState([]);
  const [editPhotoEntries, setEditPhotoEntries] = useState([]);
  const [editRemovedPhotoIds, setEditRemovedPhotoIds] = useState([]);
  const addPhotoEntriesRef = useRef([]);
  const editPhotoEntriesRef = useRef([]);

  useEffect(() => {
    addPhotoEntriesRef.current = addPhotoEntries;
  }, [addPhotoEntries]);

  useEffect(() => {
    editPhotoEntriesRef.current = editPhotoEntries;
  }, [editPhotoEntries]);

  useEffect(() => () => {
    revokeLocalPhotoEntries(addPhotoEntriesRef.current);
    revokeLocalPhotoEntries(editPhotoEntriesRef.current);
  }, []);

  const { data: platforms = [] } = useQuery({ queryKey: ["platforms"], queryFn: api.listPlatforms });
  const { data: productGroups = [] } = useQuery({ queryKey: ["productGroups"], queryFn: api.listProductGroups });
  const { data: summary } = useQuery({ queryKey: ["portfolio"], queryFn: api.portfolioSummary });
  // Unter ["portfolio", ...]: jede Invalidierung des Portfolios zieht den Zielstand mit.
  const { data: goalProgress } = useQuery({ queryKey: ["portfolio", "goals"], queryFn: api.goalProgress });
  // Ebenso das Cockpit: Inserat vorbereiten, Einstell-Auftraege und Fotos laden es neu.
  const { data: cockpit } = useQuery({ queryKey: ["portfolio", "cockpit"], queryFn: api.salesCockpit });
  const { data: items, isLoading } = useQuery({
    queryKey: ["inventory", typeFilter, groupFilter],
    queryFn: () =>
      api.listInventory({
        status: "HOLDING",
        // Backend-Default sind 100 Zeilen; ohne Limit fallen die aeltesten Posten still weg.
        limit: 500,
        ...(typeFilter && { item_type: typeFilter }),
        ...(groupFilter && { product_group: groupFilter }),
      }),
  });

  function closeAddModal() {
    revokeLocalPhotoEntries(addPhotoEntries);
    setAddPhotoEntries([]);
    setAddForm(emptyAddForm());
    setAddModal(false);
    setDuplicates([]);
  }

  function closeEditModal() {
    revokeLocalPhotoEntries(editPhotoEntries);
    setEditPhotoEntries([]);
    setEditRemovedPhotoIds([]);
    setEditModal(null);
    setEditForm({});
  }

  function appendAddPhotos(fileList) {
    if (fileList?.length) setAddPhotoEntries((prev) => [...prev, ...createLocalPhotoEntries(fileList)]);
  }

  function appendEditPhotos(fileList) {
    if (fileList?.length) setEditPhotoEntries((prev) => [...prev, ...createLocalPhotoEntries(fileList)]);
  }

  function removeLocalPhoto(setter, photoId) {
    setter((prev) => {
      const removed = prev.find((entry) => entry.id === photoId);
      if (removed) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      return prev.filter((entry) => entry.id !== photoId);
    });
  }

  const sellMutation = useMutation({
    mutationFn: ({ id, data }) => api.sellInventory(id, data),
    onSuccess: (response) => {
      setSellModal(null);
      setSellPrice("");
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      queryClient.invalidateQueries({ queryKey: ["history"] });
      const stillOpen = (response?.listings || []).some((x) => x.status === "ACTIVE" || x.status === "PAUSED");
      if (stillOpen) {
        setListingItem(response);
      }
    },
  });

  const splitMutation = useMutation({
    mutationFn: (id) => api.splitInventory(id, 1),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
  });

  const revalueMutation = useMutation({
    mutationFn: (id) => api.revalueItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id) => api.deleteInventory(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });

  const makePrimaryPhotoMutation = useMutation({
    mutationFn: ({ itemId, photoId }) => api.makePrimaryInventoryPhoto(itemId, photoId),
    onSuccess: (photos, variables) => {
      queryClient.setQueryData(["inventory"], (current) => {
        if (!Array.isArray(current)) return current;
        return current.map((item) => (item.id === variables.itemId ? { ...item, photos } : item));
      });
      setEditModal((prev) => (
        prev && prev.id === variables.itemId
          ? { ...prev, photos }
          : prev
      ));
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
  });

  const editMutation = useMutation({
    mutationFn: async ({ id, data, photoFiles, deletedPhotoIds }) => {
      const updated = await api.updateInventory(id, data);
      for (const photoId of deletedPhotoIds) {
        await api.deleteInventoryPhoto(id, photoId);
      }
      if (photoFiles.length > 0) {
        await api.uploadInventoryPhotos(id, photoFiles);
      }
      return updated;
    },
    onSuccess: () => {
      closeEditModal();
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      queryClient.invalidateQueries({ queryKey: ["history"] });
    },
  });

  const addMutation = useMutation({
    mutationFn: async ({ photoFiles, ...data }) => {
      const item = await api.addInventory(data);
      if (photoFiles.length > 0) {
        await api.uploadInventoryPhotos(item.id, photoFiles);
      }
      return item;
    },
    onSuccess: () => {
      closeAddModal();
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });

  const handleSell = () => {
    if (!sellModal || !sellPrice) return;
    sellMutation.mutate({
      id: sellModal.id,
      data: { sell_price: parseFloat(sellPrice), sell_date: sellDate, sell_platform: sellPlatform || null },
    });
  };

  const handleSetNumberChange = async (setNumber) => {
    setAddForm((prev) => ({ ...prev, set_number: setNumber }));
    if (/^\d{4,6}$/.test(setNumber.trim())) {
      setLookupLoading(true);
      try {
        const info = await api.lookupSet(setNumber.trim());
        if (info.found) {
          setAddForm((prev) => ({
            ...prev,
            set_name: info.set_name || prev.set_name,
            theme: info.theme || prev.theme,
          }));
        }
      } catch {
        // User can fill data manually.
      } finally {
        setLookupLoading(false);
      }
    }
  };

  // Both resets below run only from a plain click handler, never from
  // inside a mutation's own onSuccess: resetting a mutation from within its
  // own hook-level onSuccess is reentrant and silently drops the pending
  // success notification (confirmed against the installed query-core: the
  // per-call onSuccess never fires and status ends at "idle" instead of
  // "success"). closeAddModal()/closeEditModal() are each called from their
  // own mutation's onSuccess, so the resets belong here on open instead —
  // every fresh modal then starts clean regardless of how the previous one
  // ended, without touching a mutation while it is still mid-dispatch.
  function openAddModal() {
    addMutation.reset();
    setAddModal(true);
  }

  const openEdit = (item) => {
    closeEditModal();
    editMutation.reset();
    setEditModal(item);
    setEditForm({
      set_name: item.set_name,
      theme: item.theme || "",
      product_group: item.product_group || "",
      search_query: item.search_query || "",
      buy_price: item.buy_price != null ? String(item.buy_price) : "",
      buy_shipping: String(item.buy_shipping),
      buy_date: item.buy_date,
      buy_platform: item.buy_platform || "",
      buy_url: item.buy_url || "",
      reference_url: item.reference_url || "",
      image_url: item.image_url || "",
      condition: item.condition,
      quantity: String(item.quantity || 1),
      notes: item.notes || "",
      storage_location: item.storage_location || "",
    });
  };

  const handleEdit = (e) => {
    e.preventDefault();
    editMutation.mutate({
      id: editModal.id,
      data: {
        set_name: editForm.set_name,
        // Warengruppe ist bei Lego-Artikeln Invariante ("Lego", siehe CONTEXT.md) - den Key
        // fuer Lego gar nicht senden, dann laesst exclude_unset den gespeicherten Wert in Ruhe.
        // Bei GENERIC nie explizit null schicken (Backend lehnt das ab): leeres Feld -> undefined
        // weglassen statt null senden.
        product_group:
          editModal.item_type === "GENERIC" ? editForm.product_group.trim() || undefined : undefined,
        search_query: editForm.search_query || null,
        theme: editForm.theme || null,
        buy_price: editForm.buy_price === "" ? null : Number(editForm.buy_price),
        buy_shipping: parseFloat(editForm.buy_shipping || "0"),
        buy_date: editForm.buy_date,
        buy_platform: editForm.buy_platform || null,
        buy_url: editForm.buy_url || null,
        reference_url: editForm.reference_url || null,
        image_url: editForm.image_url || null,
        condition: editForm.condition,
        quantity: parseInt(editForm.quantity || "1", 10),
        notes: editForm.notes || null,
        storage_location: editForm.storage_location || null,
      },
      photoFiles: editPhotoEntries.map((entry) => entry.file),
      deletedPhotoIds: editRemovedPhotoIds,
    });
  };

  const handleAdd = (e) => {
    e.preventDefault();
    addMutation.mutate({
      ...addForm,
      buy_price: addForm.buy_price === "" ? null : Number(addForm.buy_price),
      buy_shipping: parseFloat(addForm.buy_shipping || "0"),
      quantity: parseInt(addForm.quantity || "1", 10),
      buy_url: addForm.buy_url || null,
      image_url: addForm.image_url || null,
      notes: addForm.notes || null,
      storage_location: addForm.storage_location || null,
      photoFiles: addPhotoEntries.map((entry) => entry.file),
    });
  };

  // "Menge erhoehen" trifft bevorzugt die Dublette am eingegebenen Lagerort,
  // sonst eine ohne Lagerort (der Ort wird dann nachgetragen), sonst die neueste.
  // Verglichen wird ohne Rand-Leerzeichen und ohne Gross-/Kleinschreibung.
  const enteredStorageLocation = addForm.storage_location.trim();
  const enteredLocationKey = normalizeLocation(enteredStorageLocation);
  const duplicateTarget =
    duplicates.length === 0
      ? null
      : (enteredLocationKey && duplicates.find((d) => normalizeLocation(d.storage_location) === enteredLocationKey)) ||
        (enteredLocationKey && duplicates.find((d) => !normalizeLocation(d.storage_location))) ||
        duplicates[0];
  // Der Zielposten liegt woanders: sein Lagerort bleibt, das muss der Hinweis sagen.
  const addStorageLocationConflict =
    !!duplicateTarget &&
    enteredLocationKey !== "" &&
    normalizeLocation(duplicateTarget.storage_location) !== "" &&
    normalizeLocation(duplicateTarget.storage_location) !== enteredLocationKey;

  const visibleItems = filterInventory(items, { listingFilter, photoFilter, search });
  const filtersActive = listingFilter !== "" || photoFilter !== "" || search.trim() !== "";

  // "Naechste 3": derselbe Weg wie "Verkaufen > Inserat vorbereiten" am Posten.
  // Danach faellt der Posten aus der Liste (er hat jetzt einen Entwurf).
  const prepareCandidate = async (candidate) => {
    setPreparingId(candidate.item_id);
    try {
      if (await prepareListingWithToast(candidate.item_id, "KLEINANZEIGEN", toast)) {
        queryClient.invalidateQueries({ queryKey: ["inventory"] });
        queryClient.invalidateQueries({ queryKey: ["portfolio"] });
        const item = (items || []).find((x) => x.id === candidate.item_id);
        if (item) setListingItem(item);
      }
    } finally {
      setPreparingId(null);
    }
  };

  return (
    <div>
      <InventoryHeader
        summary={summary}
        goalProgress={goalProgress}
        cockpit={cockpit}
        onPhotoFirst={() => setPhotoFirstModal(true)}
        onAdd={openAddModal}
        onShowWithoutPhoto={() => { setTypeFilter("LEGO"); setPhotoFilter("without"); }}
        onShowReady={() => { setListingFilter("ready"); setPhotoFilter(""); }}
        onPrepareCandidate={prepareCandidate}
        preparingId={preparingId}
      />

      <InventoryFilters
        typeFilter={typeFilter}
        setTypeFilter={setTypeFilter}
        groupFilter={groupFilter}
        setGroupFilter={setGroupFilter}
        listingFilter={listingFilter}
        setListingFilter={setListingFilter}
        photoFilter={photoFilter}
        setPhotoFilter={setPhotoFilter}
        search={search}
        setSearch={setSearch}
        productGroups={productGroups}
        totalCount={items?.length ?? 0}
        visibleCount={visibleItems.length}
      />

      <InventoryList
        items={items}
        visibleItems={visibleItems}
        isLoading={isLoading}
        filtersActive={filtersActive}
        onResetFilters={() => { setListingFilter(""); setPhotoFilter(""); setSearch(""); }}
        onEdit={openEdit}
        onPrepared={(item) => {
          queryClient.invalidateQueries({ queryKey: ["inventory"] });
          queryClient.invalidateQueries({ queryKey: ["portfolio", "cockpit"] });
          setListingItem(item);
        }}
        onMarkSold={(item) => { setSellModal(item); setSellPrice(""); setSellPlatform(""); }}
        onOpenListings={setListingItem}
        onDelete={(item) => deleteMutation.mutate(item.id)}
        revalueMutation={revalueMutation}
        splitMutation={splitMutation}
      />

      {sellModal && (
        <InventorySellDialog
          sellModal={sellModal}
          setSellModal={setSellModal}
          sellPrice={sellPrice}
          setSellPrice={setSellPrice}
          sellDate={sellDate}
          setSellDate={setSellDate}
          sellPlatform={sellPlatform}
          setSellPlatform={setSellPlatform}
          handleSell={handleSell}
          sellMutation={sellMutation}
        />
      )}

      {editModal && (
        <InventoryDetail
          editModal={editModal}
          editForm={editForm}
          setEditForm={setEditForm}
          handleEdit={handleEdit}
          productGroups={productGroups}
          platforms={platforms}
          editRemovedPhotoIds={editRemovedPhotoIds}
          setEditRemovedPhotoIds={setEditRemovedPhotoIds}
          makePrimaryPhotoMutation={makePrimaryPhotoMutation}
          editPhotoEntries={editPhotoEntries}
          appendEditPhotos={appendEditPhotos}
          removeLocalPhoto={removeLocalPhoto}
          setEditPhotoEntries={setEditPhotoEntries}
          closeEditModal={closeEditModal}
          editMutation={editMutation}
        />
      )}

      {addModal && (
        <InventoryAddDialog
          productGroups={productGroups}
          platforms={platforms}
          removeLocalPhoto={removeLocalPhoto}
          editMutation={editMutation}
          addForm={addForm}
          setAddForm={setAddForm}
          handleAdd={handleAdd}
          handleSetNumberChange={handleSetNumberChange}
          lookupLoading={lookupLoading}
          duplicates={duplicates}
          setDuplicates={setDuplicates}
          duplicateTarget={duplicateTarget}
          addStorageLocationConflict={addStorageLocationConflict}
          enteredStorageLocation={enteredStorageLocation}
          addPhotoEntries={addPhotoEntries}
          appendAddPhotos={appendAddPhotos}
          setAddPhotoEntries={setAddPhotoEntries}
          closeAddModal={closeAddModal}
          addMutation={addMutation}
        />
      )}

      {listingItem && (
        <ListingManager
          item={listingItem}
          onClose={() => setListingItem(null)}
          onChanged={() => {
            queryClient.invalidateQueries({ queryKey: ["inventory"] });
            queryClient.invalidateQueries({ queryKey: ["portfolio"] });
          }}
        />
      )}

      {photoFirstModal && (
        <PhotoFirstModal
          onClose={() => setPhotoFirstModal(false)}
          onCreated={() => {
            setPhotoFirstModal(false);
            queryClient.invalidateQueries({ queryKey: ["inventory"] });
            queryClient.invalidateQueries({ queryKey: ["productGroups"] });
          }}
        />
      )}
    </div>
  );
}
