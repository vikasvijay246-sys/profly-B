/**
 * Enhanced File Handling for Mobile Apps
 * Direct file access, native-style file picker, progress tracking
 */
const PropFlowFileManager = (() => {
  let activeFile = null;

  return {
    /**
     * Create a native-style file picker
     */
    createFilePicker(accept = '*', multiple = false, onSelect = null) {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = accept;
      input.multiple = multiple;
      input.capture = 'environment'; // Use device camera/storage on mobile

      input.addEventListener('change', (e) => {
        const files = Array.from(e.target.files);
        if (onSelect) onSelect(files);
        PropFlowNotifications.info(`${files.length} file(s) selected`);
      });

      return input;
    },

    /**
     * Quick file picker trigger
     */
    pickFiles(accept = '*', multiple = false) {
      return new Promise((resolve) => {
        const input = this.createFilePicker(accept, multiple, (files) => {
          resolve(files);
        });
        input.click();
      });
    },

    /**
     * Upload file with progress tracking
     */
    async uploadFile(file, url, onProgress = null) {
      const formData = new FormData();
      formData.append('file', file);

      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();

        if (onProgress) {
          xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
              const percentComplete = (e.loaded / e.total) * 100;
              onProgress(percentComplete);
            }
          });
        }

        xhr.addEventListener('load', () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const response = JSON.parse(xhr.responseText);
              PropFlowNotifications.success('File uploaded', file.name);
              resolve(response);
            } catch (e) {
              resolve(xhr.responseText);
            }
          } else {
            PropFlowNotifications.error('Upload failed', `Status: ${xhr.status}`);
            reject(new Error(`Upload failed with status ${xhr.status}`));
          }
        });

        xhr.addEventListener('error', () => {
          PropFlowNotifications.error('Upload error', 'Network error occurred');
          reject(new Error('Upload failed: Network error'));
        });

        xhr.addEventListener('abort', () => {
          PropFlowNotifications.warning('Upload cancelled');
          reject(new Error('Upload cancelled'));
        });

        xhr.open('POST', url);
        xhr.send(formData);
      });
    },

    /**
     * Batch upload files
     */
    async uploadFiles(files, url, onProgressAll = null) {
      const results = [];
      for (let i = 0; i < files.length; i++) {
        try {
          const result = await this.uploadFile(files[i], url, (percent) => {
            const overallPercent = ((i + percent / 100) / files.length) * 100;
            if (onProgressAll) onProgressAll(overallPercent);
          });
          results.push({ file: files[i].name, status: 'success', result });
        } catch (err) {
          results.push({ file: files[i].name, status: 'error', error: err.message });
        }
      }
      return results;
    },

    /**
     * Get file from camera (mobile only)
     */
    async takePhoto() {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.capture = 'environment';

      return new Promise((resolve) => {
        input.addEventListener('change', (e) => {
          const file = e.target.files[0];
          if (file) {
            PropFlowNotifications.info('Photo captured');
            resolve(file);
          } else {
            resolve(null);
          }
        });
        input.click();
      });
    },

    /**
     * Create shareable link for file (mobile Share API)
     */
    async shareFile(title, text, url) {
      if (!navigator.share) {
        PropFlowNotifications.warning('Share not supported on this device');
        return;
      }

      try {
        await navigator.share({
          title,
          text,
          url
        });
      } catch (err) {
        if (err.name !== 'AbortError') {
          PropFlowNotifications.error('Share failed');
        }
      }
    },

    /**
     * Download file
     */
    downloadFile(url, filename) {
      const a = document.createElement('a');
      a.href = url;
      a.download = filename || 'download';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      PropFlowNotifications.success('Download started', filename);
    }
  };
})();

window.PropFlowFileManager = PropFlowFileManager;
