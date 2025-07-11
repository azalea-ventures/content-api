# Google Cloud Storage Permissions Setup Guide

## Overview

This guide explains the required permissions for your service account to access Google Cloud Storage buckets for uploading split PDF files.

## Required Permissions

### 1. Service Account Roles

Your service account needs the following IAM roles for the bucket:

#### Minimum Required Roles:
- **Storage Object Admin** (`roles/storage.objectAdmin`)
  - Allows uploading, downloading, and deleting objects
  - Allows reading bucket metadata
  - Required for: `storage.objects.create`, `storage.objects.get`, `storage.objects.delete`

#### Optional Roles (for additional functionality):
- **Storage Object Viewer** (`roles/storage.objectViewer`)
  - Allows reading objects and bucket metadata
  - Useful for checking if files exist
- **Storage Admin** (`roles/storage.admin`)
  - Full access to all storage resources
  - **Warning**: Very broad permissions, use with caution

### 2. Specific Permissions Needed

The following specific permissions are required:

```
storage.objects.create    # Upload files
storage.objects.get       # Read files (for existence checks)
storage.objects.delete    # Delete files (optional, for cleanup)
storage.buckets.get       # Read bucket metadata
```

## Setup Instructions

### Step 1: Access Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project
3. Navigate to **IAM & Admin** > **IAM**

### Step 2: Find Your Service Account

1. Look for your service account in the IAM list
2. The service account email should match what you see in the error messages
3. Click the edit (pencil) icon next to your service account

### Step 3: Add Required Roles

1. Click **Add another role**
2. Search for and select **Storage Object Admin**
3. Click **Save**

### Step 4: Verify Bucket Access

1. Go to **Cloud Storage** > **Buckets**
2. Click on your bucket name
3. Go to the **Permissions** tab
4. Verify your service account has the necessary permissions

## Alternative: Bucket-Level Permissions

If you prefer bucket-level permissions instead of project-level IAM roles:

### Step 1: Access Bucket Permissions

1. Go to **Cloud Storage** > **Buckets**
2. Click on your bucket name
3. Go to the **Permissions** tab

### Step 2: Add Service Account

1. Click **Add**
2. Enter your service account email
3. Grant the following roles:
   - **Storage Object Admin**
   - **Storage Object Viewer** (optional)

### Step 3: Save Changes

1. Click **Save**
2. Wait a few minutes for permissions to propagate

## Testing Permissions

### Method 1: Use the Test Script

Run the provided test script to verify permissions:

```bash
python test_gcs.py
```

### Method 2: Manual Testing

You can test permissions manually using the Google Cloud Console:

1. Go to **Cloud Storage** > **Buckets**
2. Click on your bucket
3. Try to upload a test file
4. Check if you can read and delete files

## Common Permission Issues

### Issue 1: "Permission 'storage.buckets.get' denied"

**Solution**: Add **Storage Object Admin** or **Storage Admin** role to your service account.

### Issue 2: "Permission 'storage.objects.create' denied"

**Solution**: Ensure your service account has **Storage Object Admin** role.

### Issue 3: "Bucket does not exist"

**Solution**: 
1. Verify the bucket name is correct
2. Ensure the bucket is in the same project as your service account
3. Check if the bucket exists in the specified region

### Issue 4: "Service account not found"

**Solution**:
1. Verify the service account email is correct
2. Ensure the service account exists in the project
3. Check if the service account is enabled

## Security Best Practices

### 1. Principle of Least Privilege

- Only grant the minimum permissions necessary
- Use **Storage Object Admin** instead of **Storage Admin** when possible
- Consider using bucket-level permissions for more granular control

### 2. Service Account Management

- Use dedicated service accounts for different purposes
- Regularly review and audit service account permissions
- Disable unused service accounts

### 3. Bucket Security

- Enable bucket versioning for data protection
- Configure lifecycle policies to manage storage costs
- Use bucket-level IAM policies for fine-grained access control

## Troubleshooting

### Check Current Permissions

To see what permissions your service account currently has:

1. Go to **IAM & Admin** > **IAM**
2. Find your service account
3. Click on the service account email
4. Review the assigned roles

### Test with gcloud CLI

You can test permissions using the gcloud CLI:

```bash
# Test bucket access
gcloud storage ls gs://your-bucket-name/

# Test file upload
echo "test" > test.txt
gcloud storage cp test.txt gs://your-bucket-name/
```

### Enable Audit Logging

Enable Cloud Storage audit logging to track access:

1. Go to **IAM & Admin** > **Audit Logs**
2. Find **Cloud Storage**
3. Enable **Data Access** and **Admin Read** logging

## Environment Variables

Make sure these environment variables are set in your `.env` file:

```env
# Google Cloud Storage Configuration
GOOGLE_CLOUD_STORAGE_BUCKET_NAME=your-bucket-name
ENABLE_GCS_UPLOINT=true

# Service Account (same as used for other Google services)
GOOGLE_SERVICE_ACCOUNT_JSON_BASE64=your-base64-encoded-service-account-json
```

## Next Steps

After setting up permissions:

1. Test the GCS service with the provided test script
2. Verify the server starts without GCS initialization errors
3. Test the `/split` endpoint to ensure files are uploaded to GCS
4. Check your GCS bucket to confirm files are being uploaded correctly

## Support

If you continue to experience permission issues:

1. Check the Google Cloud Console audit logs
2. Verify the service account has the correct roles
3. Ensure the bucket exists and is accessible
4. Contact your Google Cloud administrator if needed 