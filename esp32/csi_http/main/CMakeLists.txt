idf_component_register(SRC_DIRS "."
                       INCLUDE_DIRS "."
                       REQUIRES
                           nvs_flash
                           esp_wifi
                           esp_netif
                           esp_event
                           esp_http_client
                           json
                           lwip
                           protocol_examples_common)
