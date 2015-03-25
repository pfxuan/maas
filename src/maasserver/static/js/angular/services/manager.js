/* Copyright 2015 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * MAAS Base Manager
 *
 * Manages a collection of items from the websocket in the browser. The manager
 * uses the RegionConnection to load the items, update the items, and listen
 * for notification events about the items.
 */

angular.module('MAAS').service(
    'Manager',
    ['$q', '$rootScope', '$timeout', 'RegionConnection', function(
            $q, $rootScope, $timeout, RegionConnection) {

        // Actions that are used to update the statuses metadata.
        var METADATA_ACTIONS = {
            CREATE: "create",
            UPDATE: "update",
            DELETE: "delete"
        };

        // Constructor
        function Manager() {
            // Primary key on the items in the list. Used to match items.
            this._pk = "id";

            // Handler on the region to call to list, create, update, delete,
            // and listen for notifications. Must be set by overriding manager.
            this._handler = null;

            // Holds all items in the system. This list must always be the same
            // object.
            this._items = [];

            // True when all of the items have been loaded. This is done on
            // intial connection to the region.
            this._loaded = false;

            // True when the items list is currently being loaded or reloaded.
            // Actions will not be processed while this is false.
            this._isLoading = false;

            // Holds all of the notify actions that need to be processed. This
            // is used to hold the actions while the items are being loaded.
            // Once all of the items are loaded the queue will be processed.
            this._actionQueue = [];

            // Holds list of all of the currently selected items. This is held
            // in a seperate list to remove the need to loop through the full
            // listing to grab the selected items.
            this._selectedItems = [];

            // Set to true when the items list should reload upon re-connection
            // to the region.
            this._autoReload = false;

            // Holds the item that is currenly being viewed. This object will
            // be updated if any notify events are recieved for it. This allows
            // the ability of not having to keep pulling the item out of the
            // items list.
            this._activeItem = null;

            // Holds metadata information that is used to helper filtering.
            this._metadata = {};

            // List of attributes to track on the loaded items. Each attribute
            // in this list will be placed in _metadata to track its currect
            // values and the number of items with that value.
            this._metadataAttributes = [];
        }

        // Return index of the item in the given array.
        Manager.prototype._getIndexOfItem = function(array, pk_value) {
            var i;
            for(i = 0, len = array.length; i < len; i++) {
                if(array[i][this._pk] === pk_value) {
                    return i;
                }
            }
            return -1;
        };

        // Replace the item in the array at the same index.
        Manager.prototype._replaceItemInArray = function(array, item) {
            var idx = this._getIndexOfItem(array, item[this._pk]);
            if(idx >= 0) {
                // Keep the current selection on the item.
                item.$selected = array[idx].$selected;
                array[idx] = item;
            }
        };

        // Remove the item from the array.
        Manager.prototype._removeItemByIdFromArray = function(
            array, pk_value) {
            var idx = this._getIndexOfItem(array, pk_value);
            if(idx >= 0) {
                array.splice(idx, 1);
            }
        };

        // Batch load items from the region in groups of 50.
        Manager.prototype._batchLoadItems = function(array, extra_func) {
            var self = this;
            var defer = $q.defer();
            var method = this._handler + ".list";
            function callLoad() {
                var params = {
                    count: 50
                };
                // Get the last pk in the list so the region knows to
                // start at that offset.
                if(array.length > 0) {
                    params.start = array[array.length-1][self._pk];
                }
                RegionConnection.callMethod(
                    method, params).then(function(items) {
                        // Pass each item to extra_func function if given.
                        if(angular.isFunction(extra_func)) {
                            angular.forEach(items, function(item) {
                                extra_func(item);
                            });
                        }

                        array.push.apply(array, items);
                        if(items.length === 50) {
                            // Could be more items, request the next 50.
                            callLoad(array);
                        } else {
                            defer.resolve(array);
                        }
                    }, defer.reject);
            }
            callLoad();
            return defer.promise;
        };

        // Return list of items.
        Manager.prototype.getItems = function() {
            return this._items;
        };

        // Load all the items.
        Manager.prototype.loadItems = function() {
            // If the items have already been loaded then, we need to
            // reload the items list not load the initial list.
            if(this._loaded) {
                return this.reloadItems();
            }

            var self = this;
            this._isLoading = true;
            return this._batchLoadItems(this._items, function(item) {
                item.$selected = false;
                self._updateMetadata(item, METADATA_ACTIONS.CREATE);
            }).then(function() {
                self._loaded = true;
                self._isLoading = false;
                self.processActions();
                return self._items;
            });
        };

        // Reload the items list.
        Manager.prototype.reloadItems = function() {
            // If the items have not been loaded then, we need to
            // load the initial list.
            if(!this._loaded) {
                return this.loadItems();
            }

            // Updates the items list with the reloaded items.
            var self = this;
            function updateItems(items) {
                // Iterate in reverse so we can remove items inline, without
                // having to adjust the index.
                var i = self._items.length;
                while(i--) {
                    var item = self._items[i];
                    var updatedIdx = self._getIndexOfItem(
                        items, item[self._pk]);
                    if(updatedIdx === -1) {
                        self._updateMetadata(item, METADATA_ACTIONS.DELETE);
                        self._items.splice(i, 1);
                        self._removeItemByIdFromArray(
                            self._selectedItems, item[self._pk]);
                    } else {
                        self._updateMetadata(
                            items[updatedIdx], METADATA_ACTIONS.UPDATE);
                        self._items[i] = items[updatedIdx];
                        items.splice(updatedIdx, 1);
                        self._replaceItemInArray(
                            self._selectedItems, self._items[i]);
                    }
                }

                // The remain items in items array are the new items.
                self._items.push.apply(self._items, items);
            }

            // The reload action loads all of the items into this list
            // instead of the items list. This list will then be used to
            // update the items list.
            var currentItems = [];

            // Start the reload process and once complete call updateItems.
            self._isLoading = true;
            return this._batchLoadItems(currentItems).then(function(items) {
                updateItems(items);
                self._isLoading = false;
                self.processActions();

                // Set the activeItem again so the region knows that its
                // the active item.
                if(angular.isObject(self._activeItem)) {
                    self.setActiveItem(self._activeItem[self._pk]);
                }

                return self._items;
            });
        };

        // Enables auto reloading of the item list on connection to region.
        Manager.prototype.enableAutoReload = function() {
            if(!this._autoReload) {
                this._autoReload = true;
                var self = this;
                this._reloadFunc = function() {
                    self.reloadItems();
                };
                RegionConnection.registerHandler("open", this._reloadFunc);
            }
        };

        // Disable auto reloading of the item list on connection to region.
        Manager.prototype.disableAutoReload = function() {
            if(this._autoReload) {
                RegionConnection.unregisterHandler("open", this._reloadFunc);
                this._reloadFunc = null;
                this._autoReload = false;
            }
        };

        // True when the initial item list has finished loading.
        Manager.prototype.isLoaded = function() {
            return this._loaded;
        };

        // True when the item list is currently being loaded or reloaded.
        Manager.prototype.isLoading = function() {
            return this._isLoading;
        };

        // Replace item in the items and selectedItems list.
        Manager.prototype._replaceItem = function(item) {
            this._updateMetadata(item, METADATA_ACTIONS.UPDATE);
            this._replaceItemInArray(this._items, item);
            this._replaceItemInArray(this._selectedItems, item);

            // Update the active item if updated item has the same primary key.
            if(angular.isObject(this._activeItem) &&
                this._activeItem[this._pk] === item[this._pk]) {
                // Copy the item into the activeItem. This keeps the reference
                // the same, not requiring another call to getActiveItem.
                angular.copy(item, this._activeItem);
            }
        };

        // Remove item in the items and selectedItems list.
        Manager.prototype._removeItem = function(pk_value) {
            var idx = this._getIndexOfItem(this._items, pk_value);
            if(idx >= 0) {
                this._updateMetadata(this._items[idx], METADATA_ACTIONS.DELETE);
            }
            this._removeItemByIdFromArray(this._items, pk_value);
            this._removeItemByIdFromArray(this._selectedItems, pk_value);
        };

        // Get the item from the list. Does not make a get request to the
        // region to load more data.
        Manager.prototype.getItemFromList = function(pk_value) {
            var idx = this._getIndexOfItem(this._items, pk_value);
            if(idx >= 0) {
                return this._items[idx];
            } else {
                return null;
            }
        };

        // Get the item from the region.
        Manager.prototype.getItem = function(pk_value) {
            var self = this;
            var method = this._handler + ".get";
            var params = {};
            params[this._pk] = pk_value;
            return RegionConnection.callMethod(
                method, params).then(function(item) {
                    self._replaceItem(item);
                    return item;
                });
        };

        // Send the update information to the region.
        Manager.prototype.updateItem = function(item) {
            var self = this;
            var method = this._handler + ".update";
            item = angular.copy(item);
            delete item.$selected;
            return RegionConnection.callMethod(
                method, item).then(function(item) {
                    self._replaceItem(item);
                    return item;
                });
        };

        // Send the delete call for item to the region.
        Manager.prototype.deleteItem = function(item) {
            var self = this;
            var method = this._handler + ".delete";
            var params = {};
            params[this._pk] = item[this._pk];
            return RegionConnection.callMethod(
                method, params).then(function() {
                    self._removeItem(item[self._pk]);
                });
        };

        // Return the active item.
        Manager.prototype.getActiveItem = function() {
            return this._activeItem;
        };

        // Set the active item.
        Manager.prototype.setActiveItem = function(pk_value) {
            if(!this._loaded) {
                throw new Error(
                    "Cannot set active item unless the manager is loaded.");
            }
            var idx = this._getIndexOfItem(this._items, pk_value);
            if(idx === -1) {
                this._activeItem = null;
                // Item with pk_value does not exists. Reject the returned
                // deferred.
                var defer = $q.defer();
                $timeout(function() {
                    defer.reject("No item with pk: " + pk_value);
                });
                return defer.promise;
            } else {
                this._activeItem = this._items[idx];
                // Data that is loaded from the list call is limited and
                // doesn't contain all of the needed data for an activeItem.
                // Call set_active on the handler for the region to know
                // this item needs all information when updated.
                var self = this;
                var method = this._handler + ".set_active";
                var params = {};
                params[this._pk] = pk_value;
                return RegionConnection.callMethod(
                    method, params).then(function(item) {
                        self._replaceItem(item);
                        return self._activeItem;
                    });
            }
        };

        // Clears the active item.
        Manager.prototype.clearActiveItem = function() {
            this._activeItem = null;
        };

        // True when the item list is stable and not being loaded or reloaded.
        Manager.prototype.canProcessActions = function() {
            return !this._isLoading;
        };

        // Handle notify from RegionConnection about an item.
        Manager.prototype.onNotify = function(action, data) {
            // Place the notification in the action queue.
            this._actionQueue.push({
                action: action,
                data: data
            });
            // Processing incoming actions is enabled. Otherwise they
            // will be queued until processActions is called.
            if(this.canProcessActions()) {
               $rootScope.$apply(this.processActions());
            }
        };

        // Process all actions to keep the item information up-to-date.
        Manager.prototype.processActions = function() {
            while(this._actionQueue.length > 0) {
                var action = this._actionQueue.shift();
                if(action.action === "create") {
                    action.data.$selected = false;
                    this._updateMetadata(
                        action.data, METADATA_ACTIONS.CREATE);
                    this._items.push(action.data);
                } else if(action.action === "update") {
                    this._replaceItem(action.data);
                } else if(action.action === "delete") {
                    this._removeItem(action.data);
                }
            }
        };

        // Return list of selected items.
        Manager.prototype.getSelectedItems = function() {
            return this._selectedItems;
        };

        // Mark the given item as selected.
        Manager.prototype.selectItem = function(pk_value) {
            var idx = this._getIndexOfItem(this._items, pk_value);
            if(idx === -1) {
                console.log(
                    "WARN: selection of " + this._handler + "(" + pk_value +
                    ") failed because its missing in the items list.");
                return;
            }

            var item = this._items[idx];
            item.$selected = true;

            idx = this._selectedItems.indexOf(item);
            if(idx === -1) {
                this._selectedItems.push(item);
            }
        };

        // Mark the given item as unselected.
        Manager.prototype.unselectItem = function(pk_value) {
            var idx = this._getIndexOfItem(this._items, pk_value);
            if(idx === -1) {
                console.log(
                    "WARN: de-selection of " + this._handler + "(" +
                    pk_value + ") failed because its missing in the " +
                    "nodes list.");
                return;
            }

            var item = this._items[idx];
            item.$selected = false;

            idx = this._selectedItems.indexOf(item);
            if(idx >= 0) {
                this._selectedItems.splice(idx, 1);
            }
        };

        // Determine if a item is selected.
        Manager.prototype.isSelected = function(pk_value) {
            var idx = this._getIndexOfItem(this._items, pk_value);
            if(idx === -1) {
                console.log(
                    "WARN: unable to determine if " + this._handler + "(" +
                    pk_value + ") is selected because its missing in the " +
                    "nodes list.");
                return false;
            }

            return this._items[idx].$selected === true;
        };

        // Return the metadata object value from `metadatas` matching `name`.
        Manager.prototype._getMetadataValue = function(metadatas, name) {
            var i;
            for(i = 0; i < metadatas.length; i++) {
                if(metadatas[i].name === name) {
                    return metadatas[i];
                }
            }
            return null;
        };

        // Add new value to metadatas if it doesnt exists or increment the
        // count if it already does.
        Manager.prototype._addMetadataValue = function(metadatas, value) {
            var metadata = this._getMetadataValue(metadatas, value);
            if(metadata) {
                metadata.count += 1;
            } else {
                metadata = {
                    name: value,
                    count: 1
                };
                metadatas.push(metadata);
            }
        };

        // Remove value from metadatas.
        Manager.prototype._removeMetadataValue = function(metadatas, value) {
            var metadata = this._getMetadataValue(metadatas, value);
            if(metadata) {
                metadata.count -= 1;
                if(metadata.count <= 0) {
                    metadatas.splice(metadatas.indexOf(metadata), 1);
                }
            }
        };

        // Update the metadata entry in `metadatas` for the array item with
        // field and based on the action.
        Manager.prototype._updateMetadataArrayEntry = function(
                metadatas, item, field, action, oldItem) {
            var self = this;

            if(action === METADATA_ACTIONS.CREATE) {
                angular.forEach(item[field], function(value) {
                    // On create ignore empty values.
                    if(value === '') {
                        return;
                    }
                    self._addMetadataValue(metadatas, value);
                });
            } else if(action === METADATA_ACTIONS.DELETE) {
                angular.forEach(item[field], function(value) {
                    self._removeMetadataValue(metadatas, value);
                });
            } else if(action === METADATA_ACTIONS.UPDATE &&
                angular.isDefined(oldItem)) {
                // Any values in added are new on the item, and any values left
                // in oldArray have been removed.
                var added = [];
                var oldArray = angular.copy(oldItem[field]);
                angular.forEach(item[field], function(value) {
                    var idx = oldArray.indexOf(value);
                    if(idx === -1) {
                        // Value not in oldArray so it has been added.
                        added.push(value);
                    } else {
                        // Value already in oldArray so its already tracked.
                        oldArray.splice(idx, 1);
                    }
                });

                // Add the new values.
                angular.forEach(added, function(value) {
                    self._addMetadataValue(metadatas, value);
                });

                // Remove the old values.
                angular.forEach(oldArray, function(value) {
                    self._removeMetadataValue(metadatas, value);
                });
            }
        };

        // Update the metadata entry in `metadatas` for the item with field and
        // based on the action.
        Manager.prototype._updateMetadataValueEntry = function(
                metadatas, item, field, action, oldItem) {
            var value = item[field];
            if(action === METADATA_ACTIONS.CREATE) {
                // On create ignore empty values.
                if(value === '') {
                    return;
                }
                this._addMetadataValue(metadatas, value);
            } else if(action === METADATA_ACTIONS.DELETE) {
                this._removeMetadataValue(metadatas, value);
            } else if(action === METADATA_ACTIONS.UPDATE) {
                // Possible to receive and update before a create if the
                // message is received out of order. So we allow the oldItem
                // not to exist.
                if(angular.isDefined(oldItem) && oldItem[field] !== value) {
                    if(oldItem[field] !== "") {
                        // Decrement the old value
                        this._removeMetadataValue(metadatas, oldItem[field]);
                    }

                    // Increment the new value with the "create"
                    // operation.
                    this._updateMetadataEntry(
                        metadatas, item, field,
                        METADATA_ACTIONS.CREATE, oldItem);
                }
            }
        };

        // Update the metadata entry in `metadatas` for the item with field and
        // based on the action.
        Manager.prototype._updateMetadataEntry = function(
                metadatas, item, field, action, oldItem) {
            if(angular.isArray(item[field])) {
                this._updateMetadataArrayEntry(
                    metadatas, item, field, action, oldItem);
            } else {
                this._updateMetadataValueEntry(
                    metadatas, item, field, action, oldItem);
            }
        };

        // Return the metadata object.
        Manager.prototype.getMetadata = function() {
            return this._metadata;
        };

        // Update the metadata objects based on the given item and action.
        Manager.prototype._updateMetadata = function(item, action) {
            var self = this;
            var oldItem, idx;
            if(action === METADATA_ACTIONS.UPDATE) {
                // Update actions require the oldItem if it exist in the
                // current item listing.
                idx = this._getIndexOfItem(this._items, item[this._pk]);
                if(idx >= 0) {
                    oldItem = this._items[idx];
                }
            }
            angular.forEach(this._metadataAttributes, function(attr) {
                if(angular.isUndefined(self._metadata[attr])) {
                    self._metadata[attr] = [];
                }
                self._updateMetadataEntry(
                    self._metadata[attr], item, attr, action, oldItem);
            });
        };

        return Manager;
    }]);
