#include <stdio.h>
#include <string.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "nvs_flash.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_http_client.h"
#include "cJSON.h"
#include "ping/ping_sock.h"
#include "lwip/inet.h"

#include "protocol_examples_common.h"

/* ── 설정 ────────────────────────────────────────────────────────── */
#define SERVER_URL   "http://192.168.0.22:5001/csi"
#define PING_INTERVAL_MS  10    /* 핑 간격 (ms) → ~100Hz CSI */
#define BATCH_SIZE        100   /* 패킷 N개 모아서 1회 POST (~1초) */

static const char *TAG = "csi_http";

/* ── 배치 통계 구조체 ─────────────────────────────────────────────── */
typedef struct {
    int   seq;
    int   rssi;
    int   channel;
    int   noise_floor;
    float amp_mean;
    float amp_std;
} csi_batch_t;

/* ── 전역 누적 변수 (CSI 콜백 전용) ──────────────────────────────── */
static QueueHandle_t s_queue;
static int   s_count       = 0;
static int   s_seq         = 0;
static float s_amp_acc     = 0.0f;
static float s_amp_sq_acc  = 0.0f;
static int   s_rssi        = 0;
static int   s_channel     = 0;
static int   s_noise       = 0;

/* ── CSI 수신 콜백 (WiFi 태스크 컨텍스트) ────────────────────────── */
static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf) return;
    if (memcmp(info->mac, ctx, 6) != 0) return; /* AP MAC만 처리 */

    const int8_t *buf = (const int8_t *)info->buf;
    int len = info->len;

    /* 서브캐리어 진폭 = sqrt(I² + Q²) 계산 후 패킷 평균 */
    float amp_sum = 0.0f, amp_sq_sum = 0.0f;
    int pairs = 0;
    for (int i = 0; i + 1 < len; i += 2) {
        float I = (float)buf[i];
        float Q = (float)buf[i + 1];
        float a = sqrtf(I * I + Q * Q);
        amp_sum    += a;
        amp_sq_sum += a * a;
        pairs++;
    }
    if (pairs == 0) return;

    float pkt_mean = amp_sum / pairs;

    if (s_count == 0) {
        s_rssi    = info->rx_ctrl.rssi;
        s_channel = info->rx_ctrl.channel;
        s_noise   = info->rx_ctrl.noise_floor;
    }

    s_amp_acc    += pkt_mean;
    s_amp_sq_acc += pkt_mean * pkt_mean;
    s_count++;

    if (s_count >= BATCH_SIZE) {
        float mean     = s_amp_acc / BATCH_SIZE;
        float variance = (s_amp_sq_acc / BATCH_SIZE) - (mean * mean);
        float std      = sqrtf(variance > 0.0f ? variance : 0.0f);

        csi_batch_t batch = {
            .seq        = s_seq++,
            .rssi       = s_rssi,
            .channel    = s_channel,
            .noise_floor = s_noise,
            .amp_mean   = mean,
            .amp_std    = std,
        };

        s_count      = 0;
        s_amp_acc    = 0.0f;
        s_amp_sq_acc = 0.0f;

        /* 논블로킹으로 큐에 전달 (WiFi 태스크 블로킹 방지) */
        xQueueSend(s_queue, &batch, 0);
    }
}

/* ── HTTP 전송 태스크 ─────────────────────────────────────────────── */
static void http_sender_task(void *arg)
{
    csi_batch_t batch;

    while (1) {
        if (xQueueReceive(s_queue, &batch, portMAX_DELAY) != pdTRUE) continue;

        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "node",        "csi");
        cJSON_AddNumberToObject(root, "seq",         batch.seq);
        cJSON_AddNumberToObject(root, "rssi",        batch.rssi);
        cJSON_AddNumberToObject(root, "channel",     batch.channel);
        cJSON_AddNumberToObject(root, "noise_floor", batch.noise_floor);
        cJSON_AddNumberToObject(root, "amp_mean",    (double)batch.amp_mean);
        cJSON_AddNumberToObject(root, "amp_std",     (double)batch.amp_std);

        char *body = cJSON_PrintUnformatted(root);
        cJSON_Delete(root);

        esp_http_client_config_t cfg = {
            .url        = SERVER_URL,
            .method     = HTTP_METHOD_POST,
            .timeout_ms = 3000,
        };
        esp_http_client_handle_t client = esp_http_client_init(&cfg);
        esp_http_client_set_header(client, "Content-Type", "application/json");
        esp_http_client_set_post_field(client, body, strlen(body));

        esp_err_t err = esp_http_client_perform(client);
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "seq=%d | amp=%.1f±%.1f | rssi=%d | HTTP %d",
                     batch.seq, batch.amp_mean, batch.amp_std, batch.rssi,
                     esp_http_client_get_status_code(client));
        } else {
            ESP_LOGW(TAG, "POST 실패: %s", esp_err_to_name(err));
        }
        esp_http_client_cleanup(client);
        free(body);
    }
}

/* ── CSI 초기화 (ESP32S3) ────────────────────────────────────────── */
static void wifi_csi_init(void)
{
    wifi_csi_config_t csi_cfg = {
        .lltf_en           = true,
        .htltf_en          = false,
        .stbc_htltf2_en    = false,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = true,
        .shift             = true,
    };

    static wifi_ap_record_t ap_info = {0};
    ESP_ERROR_CHECK(esp_wifi_sta_get_ap_info(&ap_info));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, ap_info.bssid));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

/* ── 라우터 핑 (CSI 패킷 유발) ───────────────────────────────────── */
static void wifi_ping_router_start(void)
{
    static esp_ping_handle_t ping_handle = NULL;

    esp_ping_config_t ping_cfg  = ESP_PING_DEFAULT_CONFIG();
    ping_cfg.count              = 0;
    ping_cfg.interval_ms        = PING_INTERVAL_MS;
    ping_cfg.task_stack_size    = 3072;
    ping_cfg.data_size          = 1;

    esp_netif_ip_info_t ip_info;
    esp_netif_get_ip_info(esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"), &ip_info);
    ESP_LOGI(TAG, "IP: " IPSTR " | GW: " IPSTR,
             IP2STR(&ip_info.ip), IP2STR(&ip_info.gw));

    ping_cfg.target_addr.u_addr.ip4.addr = ip4_addr_get_u32(&ip_info.gw);
    ping_cfg.target_addr.type            = ESP_IPADDR_TYPE_V4;

    esp_ping_callbacks_t cbs = {0};
    esp_ping_new_session(&ping_cfg, &cbs, &ping_handle);
    esp_ping_start(ping_handle);
}

/* ── app_main ─────────────────────────────────────────────────────── */
void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(example_connect());

    s_queue = xQueueCreate(8, sizeof(csi_batch_t));
    xTaskCreate(http_sender_task, "http_sender", 8192, NULL, 5, NULL);

    wifi_csi_init();
    wifi_ping_router_start();
}
